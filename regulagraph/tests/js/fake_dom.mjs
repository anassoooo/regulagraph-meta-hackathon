// Minimal DOM stand-in, just enough to run the real app/web/app.js controller under `node --test`
// (no jsdom, no dependencies). Supports the selectors app.js uses: tag, .class, #id, [attr], [attr="v"],
// and descendant combinations of those.

class FakeText {
  constructor(text) {
    this.textContent = String(text);
    this.parent = null;
  }
}

class ClassList {
  constructor(element) {
    this.element = element;
  }
  #set() {
    return new Set(this.element.className.split(/\s+/).filter(Boolean));
  }
  contains(name) {
    return this.#set().has(name);
  }
  toggle(name, force) {
    const set = this.#set();
    const on = force === undefined ? !set.has(name) : Boolean(force);
    if (on) set.add(name);
    else set.delete(name);
    this.element.className = [...set].join(" ");
    return on;
  }
}

export class FakeElement {
  constructor(tag, ownerDocument) {
    this.tagName = tag.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.attributes = new Map();
    this.children = [];
    this.parent = null;
    this.listeners = new Map();
    this.hidden = false;
    this.className = "";
    this.classList = new ClassList(this);
    this.dataset = {};
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = String(value);
  }
  getAttribute(name) {
    if (name === "class") return this.className || null;
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }
  append(...nodes) {
    for (const n of nodes) {
      const node = n instanceof FakeElement || n instanceof FakeText ? n : new FakeText(n);
      node.parent = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) {
    for (const c of this.children) c.parent = null;
    this.children = [];
    this.append(...nodes);
  }
  get textContent() {
    return this.children.map((c) => c.textContent).join("");
  }
  set textContent(value) {
    this.replaceChildren(new FakeText(value));
  }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }
  dispatch(type, extra = {}) {
    const event = { type, target: this, preventDefault() {}, ...extra };
    for (const fn of this.listeners.get(type) || []) fn(event);
  }
  click() {
    this.dispatch("click");
  }
  *descendants() {
    for (const c of this.children) {
      if (c instanceof FakeElement) {
        yield c;
        yield* c.descendants();
      }
    }
  }
  querySelectorAll(selector) {
    const parts = selector.trim().split(/\s+/);
    return [...this.descendants()].filter((e) => matchesChain(e, parts));
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

function matchesSimple(e, simple) {
  const re = /([#.]?[\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g;
  let m;
  let any = false;
  while ((m = re.exec(simple))) {
    any = true;
    if (m[1]) {
      const t = m[1];
      if (t.startsWith(".") && !e.classList.contains(t.slice(1))) return false;
      if (t.startsWith("#") && e.getAttribute("id") !== t.slice(1)) return false;
      if (/^[\w-]/.test(t) && e.tagName !== t.toUpperCase()) return false;
    } else {
      const v = e.getAttribute(m[2]);
      if (v === null || (m[3] !== undefined && v !== m[3])) return false;
    }
  }
  return any;
}

function matchesChain(e, parts) {
  if (!matchesSimple(e, parts[parts.length - 1])) return false;
  let rest = parts.slice(0, -1);
  let node = e.parent;
  while (rest.length && node) {
    if (node instanceof FakeElement && matchesSimple(node, rest[rest.length - 1])) rest = rest.slice(0, -1);
    node = node.parent;
  }
  return rest.length === 0;
}

export class FakeDocument {
  constructor() {
    this.body = new FakeElement("body", this);
  }
  createElement(tag) {
    return new FakeElement(tag, this);
  }
  createElementNS(_ns, tag) {
    return new FakeElement(tag, this);
  }
  // Elements from index.html are created on first lookup and attached to the body.
  getElementById(id) {
    let e = this.body.querySelector(`#${id}`);
    if (!e) {
      e = this.createElement(id === "graph" ? "svg" : "div");
      e.setAttribute("id", id);
      if (id === "graph-wrap" || id === "state" || id === "rejected") e.hidden = true;
      this.body.append(e);
    }
    return e;
  }
  querySelectorAll(selector) {
    return this.body.querySelectorAll(selector);
  }
  querySelector(selector) {
    return this.body.querySelector(selector);
  }
}
