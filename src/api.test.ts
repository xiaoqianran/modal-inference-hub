import { describe, expect, it } from "vitest";
import { isExperimentActive, parseModalCredentials } from "./api";

describe("experiment polling policy", () => {
  it("polls every non-terminal provider projection", () => {
    expect(isExperimentActive("generating-images")).toBe(true);
    expect(isExperimentActive("asset3d-uncertain")).toBe(true);
    expect(isExperimentActive("asset3d-connection_required")).toBe(true);
    expect(isExperimentActive("asset3d-running")).toBe(true);
  });

  it("stops after an explicit terminal projection", () => {
    expect(isExperimentActive("complete")).toBe(false);
    expect(isExperimentActive("asset3d-failed")).toBe(false);
    expect(isExperimentActive("select-image")).toBe(false);
  });
});

describe("parseModalCredentials", () => {
  const id = "ak-example-id";
  const secret = "as-example-secret";

  it("imports the minimal modal token set form without executing it", () => {
    expect(
      parseModalCredentials(`modal token set --token-secret '${secret}' --token-id=${id}`),
    ).toEqual({ tokenId: id, tokenSecret: secret });
  });

  it("recognizes raw credentials in either order", () => {
    expect(parseModalCredentials(`${secret} ${id}`)).toEqual({
      tokenId: id,
      tokenSecret: secret,
    });
  });

  it.each([
    `modal token set --token-id ${id}`,
    `modal token set --token-id ${id} --token-secret ${secret} --profile unsafe`,
    `modal token set --token-id ${id} --token-secret ${secret}; whoami`,
    `modal token set --token-id ${id}\n--token-secret ${secret}`,
  ])("rejects incomplete or executable input", (value) => {
    expect(() => parseModalCredentials(value)).toThrow();
  });
});
