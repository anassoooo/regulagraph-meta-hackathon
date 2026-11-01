"""Local live investigation with human review (Sprint 3.5).

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
   reservation is settled at the cost of the reported usage only if that usage is trustworthy
   (``trusted_usage``: nonnegative integers, consistent totals, reasoning within completion, output
   within the cap, input within the prompt bound); otherwise the worst case is kept. A settlement
   is never negative and never exceeds its reservation, so it can never credit the ledger. An
   unsettled reservation (crash during a call, or a failed settlement write) counts as spent at the
   worst case, and a failed settlement also blocks further calls in this process until restart. A
   ledger whose chain or amounts do not verify blocks all calls.
5. **Reviews never touch reports.** A finalized run is written once (exclusive create) and never
   rewritten. A review is a separate record in an appended, hash-chained log, bound to
   (report_id, content_digest, relationship_id). It is a reviewer's opinion: the relationship stays
   an AI-generated candidate, and no review is a legal confirmation.

Costs are estimates from published token prices (``evaluation.approx_cost_usd``), not provider
invoices.

Limits of the hash chain: it detects modification, reordering or removal of linked records, but not
truncation to an earlier valid prefix (deleting the last records, or the whole file), because there
is no trusted external anchor. Deleting the last ledger records therefore restores budget, and
deleting the last review records removes reviews, without detection. This is a guard for a local
demo, not a tamper-proof store or an account spending cap.
"""

import json
import math
import re
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

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
MAX_NOTE_CHARS = 1000
HARD_BUDGET_CEILING_USD = 5.0  # PM authorisation for Sprint 3.5; settings cannot exceed it
PROMPT_TOKEN_OVERHEAD = 64  # chat-template tokens per request, on top of the prompt's UTF-8 bytes
REVIEW_DECISIONS = ("accepted", "rejected", "needs_more_evidence")
_REPORT_ID = re.compile(r"^rep_[0-9a-f]{32}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_CONTROL_EXCEPT_NEWLINE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
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
_settlement_failed = threading.Event()  # latched when a settlement fails; blocks calls until restart
_store_lock = threading.RLock()  # serialises reads and appends of the ledger, runs and reviews


class ChainedLog:
    """JSON lines where each record carries the SHA-256 of the previous one.

    Appending never rewrites earlier lines. Reading verifies the whole chain, so an edited or
    reordered record, or one removed before the last, is detected and the log is refused (fail
    closed). Truncation to an earlier valid prefix (removing the last records, or the file) is not
    detected: there is no trusted external anchor.
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


def prompt_token_bound(messages: Sequence[ChatMessage]) -> int:
    """Upper bound: a byte-level tokenizer never emits more tokens than UTF-8 bytes."""
    return sum(len(m.content.encode("utf-8")) for m in messages) + PROMPT_TOKEN_OVERHEAD


def worst_case_cost(messages: Sequence[ChatMessage], max_completion_tokens: int) -> float:
    return approx_cost_usd(prompt_token_bound(messages), max_completion_tokens)


def _count(value: object) -> bool:
    return type(value) is int and value >= 0  # bool, float and str are not token counts


def _amount(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def trusted_usage(result: CompletionResult, max_completion_tokens: int, prompt_bound: int) -> str | None:
    """Return why the reported usage cannot be used for billing, or None if it can.

    Only trustworthy usage may lower a reservation; anything malformed or contradictory keeps the
    worst case.
    """
    usage = result.usage
    if usage is None:
        return "no usage reported"
    counts = {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
              "total_tokens": usage.total_tokens}
    if usage.completion_tokens_details is not None:
        counts["reasoning_tokens"] = usage.completion_tokens_details.reasoning_tokens
    bad = sorted(k for k, v in counts.items() if not _count(v))
    if bad:
        return f"malformed usage ({', '.join(bad)} not a nonnegative integer)"
    prompt, completion = usage.prompt_tokens, usage.completion_tokens
    if usage.total_tokens != prompt + completion:
        return "inconsistent usage (total_tokens is not prompt_tokens + completion_tokens)"
    if counts.get("reasoning_tokens", 0) > completion:
        return "inconsistent usage (reasoning_tokens exceed completion_tokens)"
    if prompt == 0:
        return "inconsistent usage (0 prompt tokens for a non-empty prompt)"
    if prompt > prompt_bound:
        return "inconsistent usage (prompt_tokens above the prompt's byte bound)"
    if completion > max_completion_tokens:
        return "inconsistent usage (completion_tokens above the output cap)"
    if completion == 0 and (result.text or result.refusal):
        return "inconsistent usage (output returned with 0 completion tokens)"
    return None


class Ledger:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.log = ChainedLog(live_dir(settings) / "ledger.jsonl")

    def _entries(self) -> tuple[dict[str, float], dict[str, float]]:
        """Reservations and settlements, after checking every amount (fail closed).

        A settlement must follow its own reservation, at most once, with an amount in
        [0, reserved]. Any other ledger content blocks live mode instead of being interpreted.
        """
        with _store_lock:
            records = self.log.read()
        reserved: dict[str, float] = {}
        settled: dict[str, float] = {}
        for r in records:
            rid, amount, kind = r.get("reservation_id"), r.get("amount_usd"), r.get("kind")
            ok = isinstance(rid, str) and _amount(amount)
            if ok and kind == "reserve":
                ok = rid not in reserved
            elif ok and kind == "settle":
                ok = rid in reserved and rid not in settled and amount <= reserved[rid]
            else:
                ok = False
            if not ok:
                raise LiveStoreUnavailable("ledger.jsonl holds an invalid entry; live mode is blocked",
                                           details={"file": "ledger.jsonl"})
            (reserved if kind == "reserve" else settled)[rid] = float(amount)
        return reserved, settled

    def state(self) -> BudgetState:
        reserved, settled = self._entries()
        spent = sum(settled.values()) + sum(v for k, v in reserved.items() if k not in settled)
        return BudgetState(budget_ceiling(self.settings), round(spent, 5), len(reserved), self.settings.live_max_calls)

    def reserve(self, amount: float) -> str:
        with _store_lock:
            return self._reserve(amount)

    def _reserve(self, amount: float) -> str:
        if _settlement_failed.is_set():
            raise LiveStoreUnavailable("A previous budget settlement could not be written; live calls are blocked until "
                                       "the server restarts. The unsettled reservation counts as spent.")
        if not _amount(amount) or amount <= 0:
            raise AllowlistViolation("invalid reservation amount")
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
        """Write the settlement. On any failure the block is latched and the reservation stays spent."""
        try:
            with _store_lock:
                reserved, settled = self._entries()
                if reservation_id not in reserved or reservation_id in settled:
                    raise LiveStoreUnavailable("the settlement does not match an open reservation")
                if not _amount(amount) or amount > reserved[reservation_id]:
                    raise LiveStoreUnavailable("the settlement amount is outside [0, reserved]")
                self.log.append({"kind": "settle", "reservation_id": reservation_id, "amount_usd": amount,
                                 "note": note, "at": _now()})
        except BaseException:
            _settlement_failed.set()
            raise


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
        # The adapter never sends more than the settings cap, so the cap is the worst case for output.
        cap = self.ledger.settings.meta_max_completion_tokens
        bound = prompt_token_bound(messages)
        reserved = approx_cost_usd(bound, cap)
        rid = self.ledger.reserve(reserved)
        self.calls += 1
        self.cost_usd = reserved  # until trustworthy usage says otherwise
        try:
            result = self.inner.complete(messages, **kwargs)
        except BaseException:
            try:
                self.ledger.settle(rid, reserved, "no usage reported (error); worst case kept")
            except BaseException:  # noqa: S110 - latched in settle; the reservation already counts as spent
                pass
            raise
        problem = trusted_usage(result, cap, bound)
        cost = None if problem else approx_cost_usd(result.usage.prompt_tokens, result.usage.completion_tokens)
        if cost is None or not _amount(cost) or cost > reserved:
            # Untrusted usage is dropped, so no token count or cost derived from it is recorded or shown.
            result = result.model_copy(update={"usage": None})
            self.ledger.settle(rid, reserved, f"{problem or 'cost outside the reservation'}; worst case kept")
        else:
            self.cost_usd = cost
            self.ledger.settle(rid, cost, "estimated from reported usage")
        return result


# --- runs and reviews -------------------------------------------------------------------

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


def reviews_log(settings: Settings) -> ChainedLog:
    return ChainedLog(live_dir(settings) / "reviews.jsonl")


Decision = Literal["accepted", "rejected", "needs_more_evidence"]


def add_review(settings: Settings, report_id: str, relationship_id: object, content_digest: object,
               decision: object, note: object) -> dict[str, Any]:
    """Append one review record. The stored run and its finalized report are only read."""
    run = load_run(settings, report_id)
    if content_digest != run.outcome.finalized.content_digest:
        raise InvalidLiveRequest("The review does not match this run's content digest.")
    if not isinstance(relationship_id, str) or relationship_id not in {
            r.relationship_id for r in run.outcome.finalized.report().relationships}:
        raise InvalidLiveRequest("The relationship is not part of this run.")
    if decision not in REVIEW_DECISIONS:
        raise InvalidLiveRequest("Unknown review decision.", details={"allowed": list(REVIEW_DECISIONS)})
    if note is None:
        note = ""
    if not isinstance(note, str) or len(note) > MAX_NOTE_CHARS or _CONTROL_EXCEPT_NEWLINE.search(note):
        raise InvalidLiveRequest(f"The note must be text of at most {MAX_NOTE_CHARS} characters.")
    with _store_lock:
        log = reviews_log(settings)
        sequence = len(log.read()) + 1
        record = log.append({
            "review_id": uuid.uuid4().hex,
            "report_id": report_id,
            "content_digest": content_digest,
            "relationship_id": relationship_id,
            "decision": decision,
            "note": redact(note.strip(), configured_secrets(settings)),
            "reviewed_at": _now(),
            # A review is an opinion recorded beside the immutable report; it changes nothing in it.
            "relationship_status": "candidate",
            "legal_confirmation": False,
        })
    # The record's 1-based position in the review log, i.e. the server's append order. The browser
    # keeps the highest one per relationship, whatever order responses arrive in. Derived, not stored.
    return {**record, "sequence": sequence}


def reviews_for(settings: Settings, report_id: str) -> list[dict[str, Any]]:
    """Review records for one run, in append order (the last one per relationship is current).

    Each carries ``sequence``, its 1-based position in the whole review log.
    """
    run = load_run(settings, report_id)
    with _store_lock:
        records = reviews_log(settings).read()
    digest = run.outcome.finalized.content_digest
    return [{**r, "sequence": i} for i, r in enumerate(records, 1)
            if r.get("report_id") == report_id and r.get("content_digest") == digest]


# --- the live investigation -------------------------------------------------------------

def budget_state(settings: Settings) -> BudgetState:
    return Ledger(settings).state()


def live_settings(settings: Settings) -> Settings:
    """Settings for the live path: the live output-token ceiling replaces META_MAX_COMPLETION_TOKENS.

    Only the live investigation uses this copy (the model call, its record and the budget
    reservation); replay and every other path keep the original settings.
    """
    return settings.model_copy(update={"meta_max_completion_tokens": settings.live_max_completion_tokens})


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
        live = live_settings(settings)
        ledger = Ledger(live)
        ledger.state()  # a corrupt ledger blocks the call before anything else happens
        guarded = GuardedAdapter(adapter or MetaModelAdapter(live), q, scope, ledger)
        outcome = investigate(q, scope.sources, scope.provisions, guarded, live)
        check_outcome(outcome, scope)
        run = LiveRun(question=q, document_ids=[view.doc_id(k) for k in keys], outcome=outcome,
                      cost_usd=guarded.cost_usd, created_at=_now())
        with _store_lock:
            save_run(settings, run)
        return run
    finally:
        _live_lock.release()

