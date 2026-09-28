// Landing-page search box: instant, client-side lookup against
// site/services.json (every OTA-declared service tos.watch has surveyed,
// tracked or not, built by datasets/build_services_catalog.py) -- no
// network round trip just to tell someone whether a name is tracked. The
// only network calls this file makes are POST /request and POST /vote,
// both real writes the reader asked for. Nothing here ever fetches an
// arbitrary URL a visitor types in (see ROADMAP.md, "on-demand check").
(function () {
  var API_BASE = window.TOS_WATCH_API_BASE || "";
  var TRACK_LABELS = {
    "ai-assistants": "AI assistants",
    dating: "Dating apps",
    typing: "Things you type into",
    "dev-tools": "Developer tools",
    consumer: "Consumer apps",
  };

  var form = document.getElementById("search-form");
  var input = document.getElementById("search-input");
  var results = document.getElementById("search-results");
  if (!form || !input || !results) return;

  var servicesPromise = fetch("services.json")
    .then(function (r) { return r.json(); })
    .catch(function () { return { services: [] }; });

  function matchKey(s) {
    return s.toLowerCase().replace(/[^a-z0-9]/g, "");
  }

  function looksLikeUrl(q) {
    return /\./.test(q) && !/\s/.test(q);
  }

  function domainKeyFromQuery(q) {
    var withScheme = /^[a-z]+:\/\//i.test(q) ? q : "https://" + q;
    try {
      var u = new URL(withScheme);
      var host = u.hostname.toLowerCase().replace(/^www\./, "");
      var parts = host.split(".").filter(Boolean);
      if (!parts.length) return null;
      var sld = parts.length >= 2 ? parts[parts.length - 2] : parts[0];
      return matchKey(sld);
    } catch (e) {
      return null;
    }
  }

  function findService(services, query) {
    var q = query.trim();
    if (!q) return null;
    if (looksLikeUrl(q)) {
      var dk = domainKeyFromQuery(q);
      if (dk) {
        var domainHit = services.filter(function (s) { return matchKey(s.name) === dk; })[0];
        if (domainHit) return domainHit;
      }
    }
    var qk = matchKey(q);
    var exact = services.filter(function (s) { return matchKey(s.name) === qk; })[0];
    if (exact) return exact;
    var ql = q.toLowerCase();
    var starts = services.filter(function (s) { return s.name.toLowerCase().indexOf(ql) === 0; })[0];
    if (starts) return starts;
    return services.filter(function (s) { return s.name.toLowerCase().indexOf(ql) !== -1; })[0] || null;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Same test the worker applies (worker/src/index.js EMAIL_RE).
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  // Worker error codes -> what the reader can do about it. Mirrors
  // subscribe.js; /request and /vote return rate_limited and invalid_*
  // today, and "blocked" is handled in case the spam filter reaches them.
  function errorMessage(code) {
    if (code === "blocked") return "Our spam filter stopped this signup. Email hello@tos.watch and we'll add you by hand.";
    if (code === "rate_limited") return "Too many tries. Wait a minute and try again.";
    if (code === "invalid_vote") return "That didn't work. Check the email address and try again.";
    if (code === "invalid_url") return "That link doesn't look right. Check it, or leave it blank.";
    if (code === "invalid_service" || code === "missing_service_or_url") return "That name didn't work. Search for a shorter name and try again.";
    return "That didn't work. Try again in a moment.";
  }

  function postJson(path, body) {
    return fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (res) { return res.json().catch(function () { return {}; }); });
  }

  function renderTracked(service) {
    var labels = (service.tracks || []).map(function (t) { return TRACK_LABELS[t] || t; });
    results.innerHTML =
      '<div class="result result-tracked">' +
      '<p class="result-status">Yes, we watch ' + esc(service.name) + ".</p>" + '<p class="result-sub">Listed under ' + esc(labels.join(" and ")) + ".</p>" +
      '<p><a class="result-link" href="s/' + esc(service.slug) + '.html">See ' +
      esc(service.name) + "’s changes and subscribe</a></p>" +
      "</div>";
  }

  var reqSeq = 0;

  function renderUntracked(opts) {
    var id = "req-" + ++reqSeq;
    var historyHtml = opts.historyLink
      ? '<p><a href="' + esc(opts.historyLink) + '">See its history on Open Terms Archive</a></p>'
      : "";
    var urlFieldHtml = opts.showUrlField
      ? '<label for="' + id + '-url" class="visually-hidden">Link to its terms (optional)</label>' +
        '<input id="' + id + '-url" name="url" type="url" placeholder="Link to its terms (optional)">'
      : "";
    results.innerHTML =
      '<div class="result result-untracked">' +
      '<p class="result-status">' + opts.heading + "</p>" +
      '<p class="result-sub">' + esc(opts.sub) + "</p>" +
      historyHtml +
      '<form class="request-form" data-service="' + esc(opts.serviceValue) + '">' +
      urlFieldHtml +
      '<label for="' + id + '-email" class="visually-hidden">Email (optional)</label>' +
      '<input id="' + id + '-email" name="email" type="email" placeholder="Email (optional)">' +
      '<button class="button button-secondary" type="submit">Request it</button>' +
      "</form>" +
      '<p class="request-note" data-for="' + id + '"></p>' +
      '<div class="mock-check">' +
      '<p class="result-sub">Coming soon: check any site yourself, right away, instead of waiting for us to add it. Leave your email to vote for it.</p>' +
      '<form class="vote-form" data-feature="on-demand-check">' +
      '<label for="' + id + '-vote-email" class="visually-hidden">Email</label>' +
      '<input id="' + id + '-vote-email" name="email" type="email" placeholder="you@example.com" required>' +
      '<button class="button button-secondary" type="submit">Tell me when it ships</button>' +
      "</form>" +
      '<p class="request-note" data-for="' + id + '-vote"></p>' +
      "</div>" +
      '<p class="self-host-note"><a href="self-hosting.html">Or run your own copy of tos.watch</a></p>' +
      "</div>";

    function wire(form, note, button, label, send, successText) {
      function show(message, isError) {
        note.textContent = message;
        note.classList.toggle("is-error", !!isError);
        if (isError) note.setAttribute("role", "alert");
        else note.removeAttribute("role");
      }
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var body = send();
        if (!body) return;
        if (body.error) { show(body.error, true); return; }
        button.disabled = true;
        button.textContent = "Sending…";
        postJson(body.path, body.data)
          .then(function (data) {
            if (data && data.ok) {
              show(successText, false);
              form.reset();
            } else {
              show(errorMessage(data && data.error), true);
            }
          })
          .catch(function () {
            show("That didn't work. Check your connection and try again.", true);
          })
          .then(function () {
            button.disabled = false;
            button.textContent = label;
          });
      });
    }

    var reqForm = results.querySelector(".request-form");
    wire(
      reqForm,
      results.querySelector('[data-for="' + id + '"]'),
      reqForm.querySelector('button[type="submit"]'),
      "Request it",
      function () {
        var data = { service: reqForm.dataset.service };
        var urlInput = reqForm.querySelector('input[name="url"]');
        if (urlInput && urlInput.value.trim()) data.url = urlInput.value.trim();
        var email = reqForm.querySelector('input[name="email"]').value.trim();
        if (email) {
          // /request quietly drops an address it can't use, so catch typos
          // here rather than let someone think they'll hear back.
          if (!EMAIL_RE.test(email)) return { error: "That email address doesn't look right. Fix it, or leave it blank." };
          data.email = email;
        }
        return { path: "/request", data: data };
      },
      "Thanks. Requests decide what we add next."
    );

    var voteForm = results.querySelector(".vote-form");
    wire(
      voteForm,
      results.querySelector('[data-for="' + id + '-vote"]'),
      voteForm.querySelector('button[type="submit"]'),
      "Tell me when it ships",
      function () {
        var email = voteForm.querySelector('input[name="email"]').value.trim();
        if (!email) return null;
        if (!EMAIL_RE.test(email)) return { error: "That email address doesn't look right. Check it and try again." };
        return { path: "/vote", data: { feature: voteForm.dataset.feature, email: email } };
      },
      "Got it. Confirm the email we send you and your vote counts."
    );
  }

  function renderArchived(service) {
    renderUntracked({
      heading: esc(service.name) + " isn't on tos.watch yet.",
      sub: "Open Terms Archive keeps its change history, though.",
      historyLink: service.ota_history_url,
      serviceValue: service.name,
      showUrlField: false,
    });
  }

  function renderNotFound(query) {
    renderUntracked({
      heading: "We don’t know “" + esc(query) + "” yet.",
      sub: "It isn't on tos.watch, and Open Terms Archive has no history for it either.",
      historyLink: null,
      serviceValue: query,
      showUrlField: true,
    });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (!q) {
      results.innerHTML = "";
      return;
    }
    servicesPromise.then(function (data) {
      var services = (data && data.services) || [];
      var hit = findService(services, q);
      if (hit && hit.status === "tracked") renderTracked(hit);
      else if (hit && hit.status === "archived") renderArchived(hit);
      else renderNotFound(q);
    });
  });
})();
