// Landing-page demand signals: one form, one email, one or more features the
// owner is deciding whether to build (a paid supporter tier, vendor watchlists
// for teams). Each ticked feature is its own POST /vote {feature, email}; the
// worker counts it anonymously and tags the subscriber in Buttondown. Nothing
// here stores anything.
(function () {
  var API_BASE = window.TOS_WATCH_API_BASE || "";
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  function errorMessage(code) {
    if (code === "rate_limited") return "Too many tries. Wait a minute and try again.";
    if (code === "invalid_vote") return "That didn't work. Check the email address and try again.";
    return "That didn't work. Try again in a moment.";
  }

  var form = document.querySelector(".signal-form");
  if (!form) return;
  var note = document.getElementById("signal-note");
  var button = form.querySelector('button[type="submit"]');
  var label = button.textContent;

  function show(message, isError) {
    note.textContent = message;
    note.classList.toggle("is-error", !!isError);
    if (isError) note.setAttribute("role", "alert");
    else note.removeAttribute("role");
  }

  // Resolves to {ok: true} or {ok: false, error: code}; never rejects.
  function vote(feature, email) {
    return fetch(API_BASE + "/vote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feature: feature, email: email }),
    })
      .then(function (res) { return res.json().catch(function () { return {}; }); })
      .then(function (data) { return data && data.ok ? { ok: true } : { ok: false, error: data && data.error }; })
      .catch(function () { return { ok: false, error: "network" }; });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var features = Array.prototype.filter
      .call(form.querySelectorAll('input[name="feature"]'), function (c) { return c.checked; })
      .map(function (c) { return c.value; });
    var email = form.querySelector('input[name="email"]').value.trim();
    if (!features.length) { show("Tick at least one of the two options above.", true); return; }
    if (!EMAIL_RE.test(email)) { show("That email address doesn't look right. Check it and try again.", true); return; }
    button.disabled = true;
    button.textContent = "Sending…";
    // One at a time, so a failure leaves a clear record of which votes went through.
    var failed = null;
    var chain = Promise.resolve();
    features.forEach(function (feature) {
      chain = chain.then(function () {
        if (failed) return;
        return vote(feature, email).then(function (r) { if (!r.ok) failed = r; });
      });
    });
    chain.then(function () {
      if (failed) show(errorMessage(failed.error), true);
      else {
        show("Thanks. If you're new here, confirm the email we send you and your vote counts.", false);
        form.reset();
      }
      button.disabled = false;
      button.textContent = label;
    });
  });
})();
