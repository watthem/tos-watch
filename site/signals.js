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
    if (!email) { show("Enter your email.", true); return; }
    if (!EMAIL_RE.test(email)) { show("That email address doesn't look right. Check it and try again.", true); return; }
    button.disabled = true;
    button.textContent = "Sending…";
    // One request per option (the worker takes one feature per /vote), one at a
    // time and stopping at the first failure, so we always know which votes
    // went through. Ticks of recorded votes are cleared so a retry can't
    // send them again.
    var names = { "founding-supporter": "the paid supporter tier", "vendor-watch": "vendor watchlists for teams" };
    var recorded = [];
    var failed = null;
    var chain = Promise.resolve();
    features.forEach(function (feature) {
      chain = chain.then(function () {
        if (failed) return;
        return vote(feature, email).then(function (r) {
          if (r.ok) {
            recorded.push(feature);
            var box = form.querySelector('input[name="feature"][value="' + feature + '"]');
            if (box) box.checked = false;
          } else {
            failed = { feature: feature, error: r.error };
          }
        });
      });
    });
    chain.then(function () {
      if (failed && recorded.length) {
        show("Your vote for " + names[recorded[0]] + " was recorded. Your vote for " + names[failed.feature] + " did not go through. " +
          errorMessage(failed.error).replace(/^That didn't work\. /, "") + " It is still ticked: send again to retry only that one.", true);
      } else if (failed) {
        show(errorMessage(failed.error) + " Nothing was recorded.", true);
      } else {
        show("Thanks. If you're new here, open the link in the email we send you: it confirms your vote and signs you up for alerts.", false);
        form.reset();
      }
      button.disabled = false;
      button.textContent = label;
    });
  });
})();
