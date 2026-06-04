const frontendBase = process.env.FRONTEND_BASE_URL || "http://localhost:3000";

async function fetchOk(path, expectedContentType) {
  const res = await fetch(`${frontendBase}${path}`);
  if (!res.ok) {
    throw new Error(`${path} returned ${res.status}`);
  }
  const contentType = res.headers.get("content-type") || "";
  if (expectedContentType && !contentType.includes(expectedContentType)) {
    throw new Error(`${path} returned content-type ${contentType}`);
  }
  return res;
}

const modelsRes = await fetchOk("/api/v1/models?limit=5", "application/json");
const models = await modelsRes.json();
if (!Array.isArray(models) || models.length === 0) {
  throw new Error("model list is empty");
}

const model = models.find((item) => item.thumbnail_url) || models[0];
const detailRes = await fetchOk(`/models/${model.id}`, "text/html");
const detailHtml = await detailRes.text();
if (detailHtml.includes("getStoredToken") || detailHtml.includes("digest")) {
  throw new Error(`/models/${model.id} rendered a server error payload`);
}
const apiDetailRes = await fetchOk(`/api/v1/models/${model.id}`, "application/json");
const apiDetail = await apiDetailRes.json();
if (apiDetail.id !== model.id || !Array.isArray(apiDetail.files)) {
  throw new Error("model detail API shape is invalid");
}

const printersRes = await fetchOk("/api/v1/printers", "application/json");
const printers = await printersRes.json();
if (!Array.isArray(printers)) {
  throw new Error("printer list API shape is invalid");
}
if (printers.length > 0) {
  const printer = printers[0];
  const printerDetailRes = await fetchOk(`/printers/${printer.id}`, "text/html");
  const printerDetailHtml = await printerDetailRes.text();
  if (
    printerDetailHtml.includes("printerId\":\"$NaN") ||
    printerDetailHtml.includes("printerId\":null")
  ) {
    throw new Error(`/printers/${printer.id} rendered an invalid printer id`);
  }
}

if (model.thumbnail_url) {
  await fetchOk(model.thumbnail_url, "image/png");
}

console.log(
  JSON.stringify(
    {
      ok: true,
      checked_model_id: model.id,
      checked_thumbnail: Boolean(model.thumbnail_url),
    },
    null,
    2,
  ),
);
