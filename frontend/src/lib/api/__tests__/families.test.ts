/** Family requests must preserve explicit identity, versions and filter semantics. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as families from "@/lib/api/families";
import { invalidateApiCache } from "@/lib/api/request";
import { expectRequest, fetchMock, lastBody, lastCall, lastForm, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
});
afterEach(() => vi.unstubAllGlobals());

describe("Family API wire contract", () => {
  it("uses canonical paths for unfiltered queries", async () => {
    respondWith({ items: [] });
    await families.listFamilies();
    expectRequest("/api/v1/families");
    await families.browseFamilies();
    expectRequest("/api/v1/families/browse");
    await families.listFamilyMembers(7);
    expectRequest("/api/v1/families/7/members");
  });
  it("requests fresh Family identity", async () => {
    respondWith({ id: 7 });
    await families.getFamily(7);
    await families.getFamily(7);
    expectRequest("/api/v1/families/7");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("encodes the Family slug", async () => {
    respondWith({ id: 7 });
    await families.getFamilyBySlug("small / large");
    expectRequest("/api/v1/families/by-slug/small%20%2F%20large");
  });
  it("preserves Family listing filters", async () => {
    respondWith({ items: [] });
    await families.listFamilies({
      q: "boat",
      collection_id: 4,
      tag: ["scaled", "boats"],
      favorites: false,
      trashed: true,
      cursor: "a+b",
      limit: 20,
      sort: "name-asc",
    });
    expectRequest(
      "/api/v1/families?q=boat&collection_id=4&tag=scaled&tag=boats&favorites=false&trashed=true&cursor=a%2Bb&limit=20&sort=name-asc",
    );
  });
  it("preserves mixed-page filters", async () => {
    respondWith({ items: [] });
    await families.browseFamilies({
      browse: "families_collapsed",
      in_family: false,
      family_role: "repaired",
      file_type: ["stl", "3mf"],
      cursor: "next",
      limit: 30,
    });
    expectRequest(
      "/api/v1/families/browse?browse=families_collapsed&in_family=false&family_role=repaired&file_type=stl&file_type=3mf&cursor=next&limit=30",
    );
  });
  it("preserves member filter false values", async () => {
    respondWith({ items: [] });
    await families.listFamilyMembers(7, {
      role: "rescaled",
      known_good: false,
      has_revisions: true,
      source: "external",
      sort: "scale-asc",
      file_type: "3mf",
    });
    expectRequest(
      "/api/v1/families/7/members?role=rescaled&known_good=false&has_revisions=true&source=external&sort=scale-asc&file_type=3mf",
    );
  });
  it("creates only the chosen canonical Model", async () => {
    respondWith({ id: 7 });
    const data = {
      name: "Boat",
      canonical_model_id: 9,
      members: [{ model_id: 8 }, { model_id: 9 }],
    };
    await families.createFamily(data);
    expectRequest("/api/v1/families", "POST");
    expect(lastBody()).toEqual(data);
  });
  it("preserves explicit metadata clearing", async () => {
    respondWith({ id: 7 });
    const data = { version: 3, description: null, collection_id: null, tags: [] };
    await families.updateFamily(7, data);
    expectRequest("/api/v1/families/7", "PATCH");
    expect(lastBody()).toEqual(data);
  });
  it("adds a versioned member", async () => {
    respondWith({ id: 15 });
    await families.addFamilyMember(7, 3, { model_id: 9, role: "repaired" });
    expectRequest("/api/v1/families/7/members", "POST");
    expect(lastBody()).toEqual({ model_id: 9, role: "repaired", version: 3 });
  });
  it("updates relative measurements explicitly", async () => {
    respondWith({ id: 15 });
    await families.updateFamilyMember(7, 15, 4, {
      scale_factor: null,
      mirrored: false,
      transformation_note: "Repaired bridge",
    });
    expectRequest("/api/v1/families/7/members/15", "PATCH");
    expect(lastBody()).toEqual({
      version: 4,
      scale_factor: null,
      mirrored: false,
      transformation_note: "Repaired bridge",
    });
  });
  it("detaches membership without deleting the Model", async () => {
    respondWith(null, 204);
    await families.detachFamilyMember(7, 15, 4);
    expectRequest("/api/v1/families/7/members/15?version=4", "DELETE");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("changes canonical with the chosen previous role", async () => {
    respondWith({ id: 7 });
    await families.setFamilyCanonical(7, 15, "rescaled", 4);
    expectRequest("/api/v1/families/7/canonical", "POST");
    expect(lastBody()).toEqual({ member_id: 15, previous_role: "rescaled", version: 4 });
  });
  it("moves membership with both Family versions", async () => {
    respondWith({ id: 15 });
    const data = { model_id: 9, source_family_id: 6, source_version: 2, destination_version: 4 };
    await families.moveFamilyMember(7, data);
    expectRequest("/api/v1/families/7/move-member", "POST");
    expect(lastBody()).toEqual(data);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("trashes only the Family", async () => {
    respondWith(null, 204);
    await families.trashFamily(7, 3);
    expectRequest("/api/v1/families/7?version=3", "DELETE");
  });
  it("restores reserved membership with a version", async () => {
    respondWith({ family: { id: 7 }, omitted_member_ids: [16] });
    const result = await families.restoreFamily(7, 4);
    expectRequest("/api/v1/families/7/restore", "POST");
    expect(lastBody()).toEqual({ version: 4 });
    expect(result.omitted_member_ids).toEqual([16]);
  });
  it.each([true, false])("sets personal Family star to %s", async (starred) => {
    respondWith(null, 204);
    await families.starFamily(7, starred);
    expectRequest("/api/v1/families/7/star", starred ? "PUT" : "DELETE");
  });
  it("sends member tag additions separately from removals", async () => {
    respondWith({ succeeded_count: 2 });
    await families.tagFamilyModels(7, 3, ["boat"], ["old"]);
    expectRequest("/api/v1/families/7/members/tags", "POST");
    expect(lastBody()).toEqual({ version: 3, add: ["boat"], remove: ["old"] });
  });
  it("moves member Models to the chosen Collection", async () => {
    respondWith({ succeeded_count: 2 });
    await families.moveFamilyModels(7, 3, "boats/small");
    expectRequest("/api/v1/families/7/members/collection", "POST");
    expect(lastBody()).toEqual({ version: 3, collection: "boats/small" });
  });
  it("stars only the server-authorized visible members", async () => {
    respondWith({ succeeded_count: 2 });
    await families.starVisibleFamilyModels(7, 3);
    expectRequest("/api/v1/families/7/members/star", "POST");
    expect(lastBody()).toEqual({ version: 3 });
  });
  it("uploads the owned cover with a version", async () => {
    respondWith({ id: 7, version: 4 });
    const cover = new File(["image"], "benchy.webp", { type: "image/webp" });
    await families.uploadFamilyCover(7, 3, cover);
    expectRequest("/api/v1/families/7/cover?version=3", "PUT");
    expect(lastForm().get("file")).toBe(cover);
    expect(lastCall().init.headers).not.toHaveProperty("Content-Type");
  });
  it("removes only the owned cover", async () => {
    respondWith({ id: 7, version: 4, cover_image_uploaded: false });
    const result = await families.removeFamilyCover(7, 3);
    expectRequest("/api/v1/families/7/cover?version=3", "DELETE");
    expect(result.cover_image_uploaded).toBe(false);
  });
  it("preserves a membership conflict for recovery", async () => {
    respondWith({ detail: "family_membership_conflict" }, 409);
    await expect(families.addFamilyMember(7, 3, { model_id: 9 })).rejects.toMatchObject({
      status: 409,
      code: "family_membership_conflict",
    });
  });
});
