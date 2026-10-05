/** Loads the actual delivery helper before the browser can start a download. */
import { downloadAuthenticatedFile } from "../../../src/lib/api/request";

const form = document.querySelector("form");
const path = document.querySelector("#artifact-path");
const button = document.querySelector("button");
if (
  !(form instanceof HTMLFormElement) ||
  !(path instanceof HTMLInputElement) ||
  !(button instanceof HTMLButtonElement)
) {
  throw new Error("delivery_harness_controls_missing");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await downloadAuthenticatedFile(path.value);
});
button.disabled = false;
