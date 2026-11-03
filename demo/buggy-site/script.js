const form = document.getElementById("contact-form");

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const data = new FormData(form);
  // Bug: sendMessage was never implemented, so submitting throws and nothing happens.
  sendMessage(Object.fromEntries(data));
  document.getElementById("form-success").hidden = false;
});
