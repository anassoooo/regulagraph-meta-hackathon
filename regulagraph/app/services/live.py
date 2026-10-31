"""Local live investigation (Sprint 3.5).

A live run is the Sprint 2 pipeline (``investigate`` -> ``finalize_report``) with a real Muse call,
over source documents the user approved. Guarantees, all enforced here on the server:

1. **Allowlist before retrieval.** ``sources`` and ``provisions_by_document`` passed to
   ``investigate`` hold only the approved documents, so retrieval, the prompt, relationship
   targets and finalization can only see them. Approval is limited to the French investigation
   documents of the manifest.
2. **Prompt check before the call.** The guarded adapter parses the source context of the outgoing
   prompt (everything after the user's question) and refuses to send it unless every document and
   provision it contains is approved and no excluded document or provision ID appears in it. The
   user's question is not source context: it may name any circular.
3. **Report check after the call.** Every document, provision and evidence span of the finalized
   report must belong to the approved documents.
4. **Spending.** One live call at a time. Before each call the worst-case cost (prompt bytes as
   input tokens, the completion cap as output tokens) is reserved in an append-only, hash-chained
   ledger; the call is refused if it could exceed the ceiling or the call cap. After the call the
   reservation is settled at the cost of the reported usage (or kept, if usage is unknown). An
   unsettled reservation (crash during a call) counts as spent. A corrupt ledger blocks all calls.
5. **Runs are immutable.** A finalized run is written once (exclusive create) and never rewritten.

Costs are estimates from published token prices (``evaluation.approx_cost_usd``), not provider
invoices.
"""

import json
import re
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core import ids
from app.core.config import Settings
from app.core.errors import RegulaGraphError
from app.core.redaction import configured_secrets, redact
from app.models.schemas import ExtractedDocument, Provision
from app.services.evaluation import CorpusView, approx_cost_usd
from app.services.investigator import InvestigationOutcome, investigate
from app.services.meta_client import ChatMessage, CompletionResult, MetaModelAdapter, ModelAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIVE_DIR = PROJECT_ROOT / "var" / "live"
LIVE_LANGUAGE = "fr"
MAX_QUESTION_CHARS = 500
HARD_BUDGET_CEILING_USD = 5.0  # PM authorisation for Sprint 3.5; settings cannot exceed it
PROMPT_TOKEN_OVERHEAD = 64  # chat-template tokens per request, on top of the prompt's UTF-8 bytes
_REPORT_ID = re.compile(r"^rep_[0-9a-f]{32}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TAG_ID = re.compile(r'<(document|provision) id="([^"]*)"')


class LiveModeDisabled(RegulaGraphError):
    code = "live_mode_disabled"
    http_status = 403


class InvalidLiveRequest(RegulaGraphError):
    code = "invalid_live_request"
    http_status = 422


class LiveBusy(RegulaGraphError):
    code = "live_busy"
    http_status = 409


class BudgetExhausted(RegulaGraphError):
    code = "live_budget_exhausted"
    http_status = 429


class AllowlistViolation(RegulaGraphError):
    """A server-side invariant failed; nothing is sent or published."""

    code = "allowlist_violation"
    http_status = 500


class LiveStoreUnavailable(RegulaGraphError):
    code = "live_store_unavailable"
    http_status = 503


class RunNotFound(RegulaGraphError):
    code = "live_run_not_found"
    http_status = 404


# --- configuration -----------------------------------------------------------------

def live_dir(settings: Settings) -> Path:
    return Path(settings.live_data_dir) if settings.live_data_dir else DEFAULT_LIVE_DIR


def budget_ceiling(settings: Settings) -> float:
    return min(settings.live_budget_usd, HARD_BUDGET_CEILING_USD)


def disabled_reason(settings: Settings) -> str | None:
    """Why paid live calls are unavailable, or None when they are enabled."""
    if not settings.live_mode_enabled:
        return "live mode is off (start with: python scripts/demo.py --live --confirm-paid-calls)"
    if not settings.meta_inference_enabled:
        return "Meta inference is disabled (META_INFERENCE_ENABLED is not true)"
    if settings.meta_api_key is None or not settings.meta_api_key.get_secret_value():
        return "MODEL_API_KEY is not configured"
    return None


def require_enabled(settings: Settings) -> None:
    reason = disabled_reason(settings)
    if reason is not None:
        raise LiveModeDisabled(f"Live mode is disabled: {reason}.")


# --- allowlist -----------------------------------------------------------------------

def live_document_keys(view: CorpusView) -> list[str]:
    """Manifest keys a user may approve: the French investigation documents, in manifest order."""
    return [e.key for e in view.corpus.documents
            if e.role == "investigation" and e.language == LIVE_LANGUAGE and e.key in view.docs]


def resolve_allowlist(document_ids: object, view: CorpusView) -> list[str]:
    """Validate approved document IDs; return their manifest keys in manifest order."""
    allowed = {view.doc_id(k): k for k in live_document_keys(view)}
    if not isinstance(document_ids, list) or not document_ids:
        raise InvalidLiveRequest("Select at least one source document.")
    if not all(isinstance(d, str) for d in document_ids) or len(set(document_ids)) != len(document_ids):
        raise InvalidLiveRequest("Document IDs must be distinct strings.")
    unknown = [d[:40] for d in document_ids if d not in allowed]
    if unknown:
        raise InvalidLiveRequest("Only the supported French BCT documents can be approved.",
                                 details={"rejected_document_ids": unknown})
    chosen = set(document_ids)
    return [k for k in live_document_keys(view) if view.doc_id(k) in chosen]


def validate_question(question: object) -> str:
    if not isinstance(question, str):
        raise InvalidLiveRequest("The question must be text.")
    q = question.strip()
    if not q:
        raise InvalidLiveRequest("Enter a question.")
    if len(q) > MAX_QUESTION_CHARS:
        raise InvalidLiveRequest(f"The question is limited to {MAX_QUESTION_CHARS} characters.")
    if _CONTROL.search(q):
        raise InvalidLiveRequest("The question must be a single line without control characters.")
    return q


@dataclass(frozen=True)
class Scope:
    """The approved and excluded parts of the corpus for one live run."""

    keys: list[str]
    sources: dict[str, ExtractedDocument]
    provisions: dict[str, list[Provision]]
    allowed_document_ids: frozenset[str]
    allowed_provision_ids: frozenset[str]
    excluded_document_ids: frozenset[str]
    excluded_provision_ids: frozenset[str]


def build_scope(keys: Sequence[str], view: CorpusView) -> Scope:
    """Approved documents only. Excluded = every other document the corpus view holds."""
    allowed = set(keys)
    sources = {view.doc_id(k): view.docs[k] for k in keys}
    provisions = {view.doc_id(k): list(view.provisions[k]) for k in keys}
    excluded = [k for k in view.docs if k not in allowed]
    return Scope(
        keys=list(keys),
        sources=sources,
        provisions=provisions,
        allowed_document_ids=frozenset(sources),
        allowed_provision_ids=frozenset(p.provision_id for ps in provisions.values() for p in ps),
        excluded_document_ids=frozenset(view.doc_id(k) for k in excluded),
        excluded_provision_ids=frozenset(p.provision_id for k in excluded for p in view.provisions[k]),
    )


def check_prompt(messages: Sequence[ChatMessage], question: str, scope: Scope) -> None:
    """Refuse a prompt whose source context holds anything outside the approved documents.

    The user's question is excluded from the check: only the text after it (documents and
    provisions, built by ``build_messages``) is source context.
    """
    user = [m for m in messages if m.role == "user"]
    prefix = f"Question: {question}\n\nDocuments:\n"
    if len(user) != 1 or not user[0].content.startswith(prefix):
        raise AllowlistViolation("prompt does not have the expected structure; nothing was sent")
    context = user[0].content[len(prefix):]
    tags = _TAG_ID.findall(context)
    docs = {v for kind, v in tags if kind == "document"}
    provs = {v for kind, v in tags if kind == "provision"}
    if not docs or not docs <= scope.allowed_document_ids or not provs <= scope.allowed_provision_ids:
        raise AllowlistViolation("prompt source context is outside the approved documents; nothing was sent")
    leaked = [i for i in scope.excluded_document_ids | scope.excluded_provision_ids if i in context]
    if leaked:
        raise AllowlistViolation("prompt source context names an excluded document; nothing was sent")


def check_outcome(outcome: InvestigationOutcome, scope: Scope) -> None:
    report = outcome.finalized.report()
    doc_ids = (set(report.document_ids) | {p.document_id for p in report.provisions}
               | {e.document_id for e in report.evidence}
               | {r.target_document_id for r in report.relationships if r.target_document_id})
    prov_ids = ({p.provision_id for p in report.provisions} | set(outcome.retrieved_provision_ids)
                | {r.source_provision_id for r in report.relationships}
                | {r.target_provision_id for r in report.relationships if r.target_provision_id})
    if not doc_ids <= scope.allowed_document_ids or not prov_ids <= scope.allowed_provision_ids:
        raise AllowlistViolation("report references a document outside the approved documents; not published")
    if any(r.status != "candidate" for r in report.relationships):
        raise AllowlistViolation("a live relationship is not a candidate; not published")


# --- hash-chained append-only logs ----------------------------------------------------

_GENESIS = "0" * 64
_live_lock = threading.Lock()  # one paid call at a time, per process
_store_lock = threading.RLock()  # serialises reads and appends of the ledger, runs and reviews


class ChainedLog:
    """JSON lines where each record carries the SHA-256 of the previous one.

    Appending never rewrites earlier lines. Reading verifies the whole chain, so an edited,
    reordered or deleted record is detected and the log is refused (fail closed).
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        prev = _GENESIS
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                rec = json.loads(line)
                if not isinstance(rec, dict) or rec.get("prev") != prev:
                    raise ValueError("chain broken")
                body = {k: v for k, v in rec.items() if k != "hash"}
                if rec.get("hash") != ids.content_digest(body):
                    raise ValueError("record hash mismatch")
                records.append(rec)
                prev = rec["hash"]
        except (OSError, ValueError, TypeError) as exc:
            raise LiveStoreUnavailable(f"{self.path.name} is unreadable or was modified; live mode is blocked",
                                       details={"file": self.path.name}) from exc
        return records

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        records = self.read()
        body = {**record, "prev": records[-1]["hash"] if records else _GENESIS}
        full = {**body, "hash": ids.content_digest(body)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(ids.canonical_json(full) + "\n")
                f.flush()
        except OSError as exc:
            raise LiveStoreUnavailable(f"cannot write {self.path.name}", details={"file": self.path.name}) from exc
        return full


# --- spending ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BudgetState:
    ceiling_usd: float
    spent_usd: float
    calls_used: int
    max_calls: int

    @property
    def remaining_usd(self) -> float:
        return round(max(self.ceiling_usd - self.spent_usd, 0.0), 5)


def worst_case_cost(messages: Sequence[ChatMessage], max_completion_tokens: int) -> float:
    """Upper bound: a byte-level tokenizer never emits more tokens than UTF-8 bytes."""
    prompt_tokens = sum(len(m.content.encode("utf-8")) for m in messages) + PROMPT_TOKEN_OVERHEAD
    return approx_cost_usd(prompt_tokens, max_completion_tokens)


class Ledger:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.log = ChainedLog(live_dir(settings) / "ledger.jsonl")

    def state(self) -> BudgetState:
        with _store_lock:
            records = self.log.read()
        reserved = {r["reservation_id"]: float(r["amount_usd"]) for r in records if r.get("kind") == "reserve"}
        settled = {r["reservation_id"]: float(r["amount_usd"]) for r in records if r.get("kind") == "settle"}
        spent = sum(settled.values()) + sum(v for k, v in reserved.items() if k not in settled)
        return BudgetState(budget_ceiling(self.settings), round(spent, 5), len(reserved), self.settings.live_max_calls)

    def reserve(self, amount: float) -> str:
        with _store_lock:
            return self._reserve(amount)

    def _reserve(self, amount: float) -> str:
        st = self.state()
        if st.calls_used >= st.max_calls:
            raise BudgetExhausted("The live call limit is reached; no call was made.",
                                  details={"calls_used": st.calls_used, "max_calls": st.max_calls})
        if st.spent_usd + amount > st.ceiling_usd:
            raise BudgetExhausted("The live budget could be exceeded by this call; no call was made.",
                                  details={"remaining_usd": st.remaining_usd, "worst_case_usd": amount})
        rid = uuid.uuid4().hex
        self.log.append({"kind": "reserve", "reservation_id": rid, "amount_usd": amount, "at": _now()})
        return rid

    def settle(self, reservation_id: str, amount: float, note: str) -> None:
        with _store_lock:
            self.log.append({"kind": "settle", "reservation_id": reservation_id, "amount_usd": amount,
                             "note": note, "at": _now()})


class GuardedAdapter:
    """Checks the prompt against the allowlist and reserves budget before the wrapped call."""

    def __init__(self, inner: ModelAdapter, question: str, scope: Scope, ledger: Ledger) -> None:
        self.inner = inner
        self.question = question
        self.scope = scope
        self.ledger = ledger
        self.cost_usd: float | None = None
        self.calls = 0

    def complete(self, messages: Sequence[ChatMessage], **kwargs: Any) -> CompletionResult:
        if self.calls:
            raise AllowlistViolation("one model call per live run")
        check_prompt(messages, self.question, self.scope)
        cap = kwargs.get("max_completion_tokens") or self.ledger.settings.meta_max_completion_tokens
        reserved = worst_case_cost(messages, min(int(cap), self.ledger.settings.meta_max_completion_tokens))
        rid = self.ledger.reserve(reserved)
        self.calls += 1
        try:
            result = self.inner.complete(messages, **kwargs)
        except BaseException:
            self.cost_usd = reserved
            self.ledger.settle(rid, reserved, "no usage reported (error); worst case kept")
            raise
        if result.usage is None:
            self.cost_usd = reserved
            self.ledger.settle(rid, reserved, "no usage reported; worst case kept")
        else:
            self.cost_usd = approx_cost_usd(result.usage.prompt_tokens, result.usage.completion_tokens)
            self.ledger.settle(rid, self.cost_usd, "estimated from reported usage")
        return result


# --- runs --------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class LiveRun:
    question: str
    document_ids: list[str]
    outcome: InvestigationOutcome
    cost_usd: float | None
    created_at: str


def _run_path(settings: Settings, report_id: str) -> Path:
    if not isinstance(report_id, str) or not _REPORT_ID.fullmatch(report_id):
        raise RunNotFound("Unknown live run.")
    return live_dir(settings) / "runs" / f"{report_id}.json"


def save_run(settings: Settings, run: LiveRun) -> None:
    """Written once (exclusive create); a finalized live run is never rewritten."""
    path = _run_path(settings, run.outcome.finalized.report_id)
    data = {"question": run.question, "document_ids": run.document_ids, "cost_usd": run.cost_usd,
            "created_at": run.created_at, "outcome": json.loads(run.outcome.model_dump_json())}
    text = redact(json.dumps(data, ensure_ascii=False, indent=2), configured_secrets(settings))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except FileExistsError as exc:
        raise LiveStoreUnavailable("a live run with this report ID already exists") from exc
    except OSError as exc:
        raise LiveStoreUnavailable("cannot write the live run") from exc


def load_run(settings: Settings, report_id: str) -> LiveRun:
    """Load a stored run; FinalizedReport re-validation rejects an edited snapshot or digest."""
    path = _run_path(settings, report_id)
    if not path.is_file():
        raise RunNotFound("Unknown live run.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        outcome = InvestigationOutcome.model_validate(data["outcome"])
        if outcome.finalized.report_id != report_id:
            raise ValueError("report_id mismatch")
        return LiveRun(question=data["question"], document_ids=list(data["document_ids"]), outcome=outcome,
                       cost_usd=data["cost_usd"], created_at=data["created_at"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LiveStoreUnavailable("the stored live run failed validation", details={"report_id": report_id}) from exc


# --- the live investigation -------------------------------------------------------------

def budget_state(settings: Settings) -> BudgetState:
    return Ledger(settings).state()


def run_live(settings: Settings, view: CorpusView, question: object, document_ids: object,
             confirm_paid_call: object, *, adapter: ModelAdapter | None = None) -> LiveRun:
    require_enabled(settings)
    if confirm_paid_call is not True:
        raise InvalidLiveRequest("Confirm the paid Meta call to run a live investigation.")
    q = validate_question(question)
    keys = resolve_allowlist(document_ids, view)
    scope = build_scope(keys, view)
    if not _live_lock.acquire(blocking=False):
        raise LiveBusy("A live investigation is already running; wait for it to finish.")
    try:
        ledger = Ledger(settings)
        ledger.state()  # a corrupt ledger blocks the call before anything else happens
        guarded = GuardedAdapter(adapter or MetaModelAdapter(settings), q, scope, ledger)
        outcome = investigate(q, scope.sources, scope.provisions, guarded, settings)
        check_outcome(outcome, scope)
        run = LiveRun(question=q, document_ids=[view.doc_id(k) for k in keys], outcome=outcome,
                      cost_usd=guarded.cost_usd, created_at=_now())
        with _store_lock:
            save_run(settings, run)
        return run
    finally:
        _live_lock.release()

