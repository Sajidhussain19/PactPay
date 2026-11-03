const form = document.getElementById("contact-form");
const success = document.getElementById("form-success");
const error = document.getElementById("form-error");

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const complete = ["name", "email", "message"].every((field) => String(data.get(field) || "").trim());
  if (!complete) {
    error.hidden = false;
    return;
  }
  // A real site would POST to a form service (e.g. Formspree) that emails the owner.
  error.hidden = true;
  form.hidden = true;
  success.hidden = false;
});
