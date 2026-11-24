/* Public synthetic credentials only. Served by Keycloak itself; no inline script or CSP exception. */
(() => {
  function fillGuestLogin() {
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
    const notice = document.createElement("section");
    notice.setAttribute("role", "note");
    const byline = document.createElement("p");
    byline.textContent = "A product of Boston AI Forward";
    const intro = document.createElement("p");
    intro.textContent = "Boston AI Forward tailors Tandem to the way your business works, from the first customer inquiry to scheduling, inventory, and follow-up.";
    const instructions = document.createElement("p");
    instructions.textContent = "Explore the demo. Sign in with the prefilled guest account, then choose a sample business. " +
      "As Demo Guest, you can explore three fictional businesses. Email is simulated. " +
      "You cannot approve proposals you asked the assistant to create. To use your own account, replace both fields.";
    const inquiry = document.createElement("a");
    inquiry.href = "https://bostonaiforward.com/inquiry.html";
    inquiry.textContent = "Discuss a custom build";
    notice.append(byline, intro, instructions, inquiry);
    form.before(notice);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fillGuestLogin, { once: true });
  } else {
    fillGuestLogin();
  }
})();
