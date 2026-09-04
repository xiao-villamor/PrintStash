// @vitest-environment node

import { createHash } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { afterEach, describe, expect, it } from "vitest";

import { buildBrowserCaptureMessage } from "../capture-adapter.ts";
import { captureRichFiles } from "../capture-transport.ts";

interface CaptureDeclaration {
  id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
}

interface CaptureCreateBody {
  source_url: string;
  title: string | null;
  capture_source: { provider: string; source_item_id: string | null };
  files: CaptureDeclaration[];
}

interface CaptureRequest {
  method: string;
  path: string;
  body: Buffer;
  json: CaptureCreateBody | null;
  authorization: string | undefined;
}

function digest(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex");
}

async function requestBody(request: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks);
}

function responseJson(response: ServerResponse, status: number, value: object): void {
  const body = JSON.stringify(value);
  response.writeHead(status, { "content-type": "application/json" });
  response.end(body);
}

describe("capture transport local HTTP contract", () => {
  let server: Server | undefined;

  afterEach(async () => {
    if (server === undefined) return;
    await new Promise<void>((resolve, reject) =>
      server?.close((error) => (error ? reject(error) : resolve())),
    );
    server = undefined;
  });

  it("selects a provider candidate, creates a real slot, PUTs raw bytes, and finalizes", async () => {
    const requests: CaptureRequest[] = [];
    const bytes = Buffer.from("local-capture-mesh");
    const serverRequest = async (request: IncomingMessage, response: ServerResponse) => {
      const body = await requestBody(request);
      const path = request.url || "/";
      const record: CaptureRequest = {
        method: request.method || "",
        path,
        body,
        json: path.endsWith("capture-upload-slots") ? JSON.parse(body.toString("utf8")) : null,
        authorization: request.headers.authorization,
      };
      requests.push(record);

      if (record.method === "POST" && path === "/api/v1/inbox/capture-upload-slots") {
        responseJson(response, 201, {
          item: { id: 321 },
          slots: [
            {
              id: "slot-local",
              role: "file",
              source_file_id: "321:benchy.3mf",
              filename: "benchy.3mf",
              media_type: "model/3mf",
              size_bytes: bytes.length,
              sha256: digest(bytes),
              state: "pending",
            },
          ],
        });
        return;
      }
      if (record.method === "PUT" && path === "/api/v1/inbox/capture-upload-slots/slot-local") {
        responseJson(response, 200, { state: "uploaded" });
        return;
      }
      if (record.method === "POST" && path === "/api/v1/inbox/321/capture-upload-finalize") {
        responseJson(response, 200, { id: 321, state: "review" });
        return;
      }
      responseJson(response, 404, { detail: "not_found" });
    };

    server = createServer((request, response) => void serverRequest(request, response));
    await new Promise<void>((resolve, reject) => {
      server?.once("error", reject).listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    if (address === null || typeof address === "string") {
      throw new Error("Local server did not start");
    }

    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/321-benchy?source=extension",
      pageTitle: "3D Benchy",
      jsonLd: [
        JSON.stringify({
          name: "3D Benchy",
          distribution: [
            {
              contentUrl: "https://media.printables.com/files/benchy.3mf?signature=fixture",
              encodingFormat: "model/3mf",
              contentSize: String(bytes.length),
            },
          ],
        }),
      ],
    });
    const captureWithMetadataCandidate = {
      ...capture,
      state: "ready" as const,
      candidates: [{ id: "321:benchy.3mf", filename: "benchy.3mf", fileType: "other" as const }],
    };
    const candidate = captureWithMetadataCandidate.candidates[0];
    if (candidate === undefined) throw new Error("Provider fixture did not produce a candidate");

    const result = await captureRichFiles({
      vault: `http://127.0.0.1:${address.port}`,
      authorization: "device-secret",
      sourceUrl: capture.source.canonical_url,
      title: capture.source.fields.title?.value,
      captureSource: capture.source,
      files: [
        {
          id: candidate.id,
          file: new Blob([bytes], { type: "model/3mf" }),
          filename: candidate.filename,
          mediaType: "model/3mf",
        },
      ],
    });

    expect(result).toEqual({ id: 321, state: "review" });
    expect(requests.map(({ method, path }) => `${method} ${path}`)).toEqual([
      "POST /api/v1/inbox/capture-upload-slots",
      "PUT /api/v1/inbox/capture-upload-slots/slot-local",
      "POST /api/v1/inbox/321/capture-upload-finalize",
    ]);
    expect(requests.every(({ authorization }) => authorization === "Bearer device-secret")).toBe(
      true,
    );
    expect(requests[0]?.json).toMatchObject({
      source_url: "https://www.printables.com/model/321-benchy",
      title: "3D Benchy",
      capture_source: { provider: "printables", source_item_id: "321" },
      files: [
        {
          id: "321:benchy.3mf",
          filename: "benchy.3mf",
          media_type: "model/3mf",
          size_bytes: bytes.length,
          sha256: digest(bytes),
        },
      ],
    });
    expect(requests[1]?.body).toEqual(bytes);
  });
});
