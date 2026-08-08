// PENDANT content script: reports user-initiated events with the full
// identity vector (invariant 4). Password-type input values NEVER
// leave the page (invariant 3, first line of defense); the collector's
// redaction registry is the second.
(() => {
  if (window.__pendantHooked) return;
  window.__pendantHooked = true;

  const IMPLICIT_ROLES = {
    a: "link", button: "button", select: "combobox", textarea: "textbox",
    nav: "navigation", main: "main", header: "banner", footer: "contentinfo",
    form: "form", table: "table", img: "img", h1: "heading", h2: "heading",
    h3: "heading", h4: "heading", h5: "heading", h6: "heading",
  };
  const INPUT_ROLES = {
    button: "button", submit: "button", reset: "button", checkbox: "checkbox",
    radio: "radio", range: "slider", number: "spinbutton", search: "searchbox",
  };

  function role(el) {
    const explicit = el.getAttribute && el.getAttribute("role");
    if (explicit) return explicit;
    const tag = el.tagName ? el.tagName.toLowerCase() : "";
    if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      return INPUT_ROLES[type] || "textbox";
    }
    if (tag === "a" && !el.hasAttribute("href")) return "";
    return IMPLICIT_ROLES[tag] || "";
  }

  function accessibleName(el) {
    const aria = el.getAttribute && el.getAttribute("aria-label");
    if (aria) return aria.trim();
    const labelledBy = el.getAttribute && el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const parts = labelledBy.split(/\s+/)
        .map((id) => { const n = document.getElementById(id); return n ? n.textContent.trim() : ""; })
        .filter(Boolean);
      if (parts.length) return parts.join(" ");
    }
    if (el.labels && el.labels.length) return el.labels[0].textContent.trim();
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
      const ph = el.getAttribute("placeholder");
      if (ph) return ph.trim();
    }
    const text = (el.textContent || "").trim().replace(/\s+/g, " ");
    if (text) return text.slice(0, 80);
    const title = el.getAttribute && el.getAttribute("title");
    if (title) return title.trim();
    const value = el.tagName === "INPUT" && (el.type === "submit" || el.type === "button") ? el.value : "";
    return value || "";
  }

  function cssPath(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 12) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift(`${part}#${CSS.escape(node.id)}`); break; }
      const testid = node.getAttribute("data-testid");
      if (testid) { parts.unshift(`[data-testid="${testid}"]`); break; }
      const siblings = node.parentElement
        ? Array.from(node.parentElement.children).filter((c) => c.tagName === node.tagName)
        : [];
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  }

  function xPath(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 20) {
      let index = 1;
      let sibling = node.previousElementSibling;
      while (sibling) { if (sibling.tagName === node.tagName) index += 1; sibling = sibling.previousElementSibling; }
      parts.unshift(`${node.tagName.toLowerCase()}[${index}]`);
      node = node.parentElement;
    }
    return "/" + parts.join("/");
  }

  const STABLE_ATTRS = ["id", "name", "type", "href", "autocomplete", "data-testid", "aria-label"];

  function vector(el) {
    const attrs = {};
    for (const attr of STABLE_ATTRS) {
      const v = el.getAttribute && el.getAttribute(attr);
      if (v) attrs[attr] = v;
    }
    let bbox = null;
    try {
      const r = el.getBoundingClientRect();
      bbox = [r.x, r.y, r.width, r.height];
    } catch (e) { /* detached */ }
    return {
      role: role(el) || null,
      name: accessibleName(el) || null,
      testid: (el.getAttribute && el.getAttribute("data-testid")) || null,
      attrs,
      css: cssPath(el) || null,
      xpath: xPath(el) || null,
      frame_url: location.href,
      bbox,
    };
  }

  function isSecretInput(el) {
    return el.tagName === "INPUT" && (el.getAttribute("type") || "").toLowerCase() === "password";
  }

  function report(kind, payload) {
    try { window.__pendant_report(JSON.stringify({ kind, ...payload })); } catch (e) { /* collector gone */ }
  }

  document.addEventListener("click", (e) => {
    if (!e.isTrusted) return;
    const el = e.target.closest("button, a, input, select, label, [role]") || e.target;
    report("click", { target: vector(el) });
  }, true);

  document.addEventListener("input", (e) => {
    if (!e.isTrusted) return;
    const el = e.target;
    if (!el || !("value" in el)) return;
    const secret = isSecretInput(el);
    report("input", {
      target: vector(el),
      value: secret ? null : String(el.value),
      secret,
      input_type: (el.getAttribute && el.getAttribute("type")) || el.tagName.toLowerCase(),
      autocomplete: (el.getAttribute && el.getAttribute("autocomplete")) || null,
    });
  }, true);

  const REPORTED_KEYS = new Set(["Enter", "Tab", "Escape"]);
  document.addEventListener("keydown", (e) => {
    if (!e.isTrusted) return;
    if (e.altKey && e.shiftKey && (e.key === "X" || e.key === "x")) {
      report("blank_request", {});
      return;
    }
    if (!REPORTED_KEYS.has(e.key)) return;
    const mods = [];
    if (e.ctrlKey) mods.push("Control");
    if (e.metaKey) mods.push("Meta");
    if (e.altKey) mods.push("Alt");
    if (e.shiftKey) mods.push("Shift");
    report("key", { keys: [...mods, e.key] });
  }, true);

  document.addEventListener("focusin", (e) => {
    if (!e.isTrusted) return;
    report("focus", { target: vector(e.target) });
  }, true);

  document.addEventListener("copy", () => report("clipboard", { op: "copy" }), true);
  document.addEventListener("paste", () => report("clipboard", { op: "paste" }), true);
})();
