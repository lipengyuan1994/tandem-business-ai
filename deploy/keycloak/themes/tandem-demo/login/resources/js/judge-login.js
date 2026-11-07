/* Public synthetic credentials only. Served by Keycloak itself; no inline script or CSP exception. */
(() => {
  function fillJudgeLogin() {
    const form = document.getElementById("kc-form-login");
    const username = form?.querySelector('input[name="username"]');
    const password = form?.querySelector('input[name="password"]');
    const credentials = window.tandemDemoCredentials;
    // A password-only reauthentication, OTP, registration or reset page must never be prefilled.
    if (!form || !username || !password || !credentials) return;
    if (username.value === "" && password.value === "") {
      username.value = credentials.email;
      password.value = credentials.password;
      username.dispatchEvent(new Event("input", { bubbles: true }));
      password.dispatchEvent(new Event("input", { bubbles: true }));
    }
    const notice = document.createElement("p");
    notice.setAttribute("role", "note");
    notice.textContent = "Judges: the demo account is filled in. Click Sign in. " +
      "Use the business switcher to explore all three businesses. " +
      "Settings and staff are protected. You cannot approve proposals you asked the assistant to create; " +
      "review proposals from other staff instead. Email is simulated. " +
      "To use your own account, clear both fields and enter your credentials.";
    form.before(notice);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fillJudgeLogin, { once: true });
  } else {
    fillJudgeLogin();
  }
})();
