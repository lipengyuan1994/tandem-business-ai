/* SMB front desk embed v1.0.0. Cache entry is revalidated hourly. */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script) return;
  var slug = script.getAttribute("data-business") || "";
  if (!/^[a-z0-9-]{3,40}$/.test(slug)) return;
  var origin = new URL(script.src).origin;
  function mount() {
    var host = document.createElement("div");
    var shadow = host.attachShadow({mode: "closed"});
    var style = document.createElement("style");
    style.textContent = ":host{position:fixed;right:16px;bottom:16px;z-index:2147483000;font:16px system-ui}button{font:inherit;cursor:pointer;min-height:44px;border:1px solid #555;border-radius:22px;background:#182c40;color:white;padding:10px 18px}button:focus-visible{outline:3px solid #f9b800;outline-offset:3px}.panel{width:min(390px,calc(100vw - 32px));height:min(600px,calc(100dvh - 100px));background:white;border:1px solid #777;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px #0004;margin-bottom:12px}.bar{display:flex;justify-content:flex-end;background:#182c40}.bar button{border:0;padding:2px 16px}iframe{display:block;width:100%;height:calc(100% - 44px);border:0}[hidden]{display:none!important}@media(prefers-reduced-motion:no-preference){button{transition:background-color .15s}}";
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = "Chat with us";
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", "smb-chat-panel");
    var panel = document.createElement("section");
    panel.className = "panel";
    panel.id = "smb-chat-panel";
    panel.hidden = true;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", button.textContent);
    var bar = document.createElement("div");
    bar.className = "bar";
    var close = document.createElement("button");
    close.type = "button";
    close.textContent = "Close chat";
    var frame = document.createElement("iframe");
    frame.title = "Business AI front desk chat";
    frame.referrerPolicy = "no-referrer";
    frame.setAttribute("allow", "camera 'none'; microphone 'none'; geolocation 'none'");
    var loaded = false;
    function send(type) {
      if (loaded && frame.contentWindow) frame.contentWindow.postMessage({type: type}, origin);
    }
    function setOpen(open) {
      panel.hidden = !open;
      button.setAttribute("aria-expanded", String(open));
      if (open) {
        if (!frame.src) frame.src = origin + "/embed/" + slug + "/chat";
        send("smb:open");
        close.focus();
      } else {
        send("smb:close");
        button.focus();
      }
    }
    frame.addEventListener("load", function () { loaded = true; if (!panel.hidden) send("smb:open"); });
    button.addEventListener("click", function () { setOpen(panel.hidden); });
    close.addEventListener("click", function () { setOpen(false); });
    shadow.addEventListener("keydown", function (event) { if (event.key === "Escape" && !panel.hidden) { event.preventDefault(); setOpen(false); } });
    window.addEventListener("message", function (event) {
      if (event.origin !== origin || event.source !== frame.contentWindow || !event.data || typeof event.data !== "object") return;
      var data = event.data;
      if (data.type === "smb:close" && Object.keys(data).length === 1) setOpen(false);
      if (data.type === "smb:resize" && Object.keys(data).length === 2 && Number.isFinite(data.height)) {
        panel.style.height = "min(" + Math.max(240, Math.min(800, Math.round(data.height))) + "px, calc(100dvh - 100px))";
      }
    });
    bar.append(close);
    panel.append(bar, frame);
    shadow.append(style, panel, button);
    document.body.append(host);
    fetch(origin + "/api/v1/public/" + slug + "/embed-config", {credentials: "omit", mode: "cors"})
      .then(function (r) { if (!r.ok) throw new Error("config"); return r.json(); })
      .then(function (data) { if (typeof data.label === "string" && data.label.length <= 80) { button.textContent = data.label; panel.setAttribute("aria-label", data.label); } })
      .catch(function () {});
  }
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount, {once: true});
}());
