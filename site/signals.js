// Landing-page demand signals: each .signal-form is a POST /vote for one
// feature the owner is deciding whether to build (a paid supporter tier,
// vendor watchlists for teams). The worker counts it anonymously and tags
// the subscriber in Buttondown; nothing here stores anything.
(function () {
  var API_BASE = window.TOS_WATCH_API_BASE || "";
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  function errorMessage(code) {
    if (code === "rate_limited") return "Too many tries. Wait a minute and try again.";
    if (code === "invalid_vote") return "That didn't work. Check the email address and try again.";
    return "That didn't work. Try again in a moment.";
  }

  Array.prototype.forEach.call(document.querySelectorAll(".signal-form"), function (form) {
    var note = form.parentNode.querySelector(".request-note");
    var button = form.querySelector('button[type="submit"]');
    var label = button.textContent;
    function show(message, isError) {
      note.textContent = message;
      note.classList.toggle("is-error", !!isError);
      if (isError) note.setAttribute("role", "alert");
      else note.removeAttribute("role");
    }
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var email = form.querySelector('input[name="email"]').value.trim();
      if (!EMAIL_RE.test(email)) { show("That email address doesn't look right. Check it and try again.", true); return; }
      button.disabled = true;
      button.textContent = "Sending…";
      fetch(API_BASE + "/vote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feature: form.dataset.feature, email: email }),
      })
        .then(function (res) { return res.json().catch(function () { return {}; }); })
        .then(function (data) {
          if (data && data.ok) { show(form.dataset.success, false); form.reset(); }
          else show(errorMessage(data && data.error), true);
        })
        .catch(function () { show("That didn't work. Check your connection and try again.", true); })
        .then(function () { button.disabled = false; button.textContent = label; });
    });
  });
})();
