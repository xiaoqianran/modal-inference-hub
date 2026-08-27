import { describe, expect, it } from "vitest";
import { parseModalCommand } from "./modalCommand";

describe("parseModalCommand", () => {
  it("parses a standard modal token set command", () => {
    expect(parseModalCommand(
      "modal token set --token-id ak-example123 --token-secret as-example456",
    )).toEqual({ tokenId: "ak-example123", tokenSecret: "as-example456" });
  });

  it("accepts flags in any order", () => {
    expect(parseModalCommand(
      "modal token set --token-secret as-secret123 --token-id ak-id123",
    )).toEqual({ tokenId: "ak-id123", tokenSecret: "as-secret123" });
  });

  it("accepts = separators and quotes", () => {
    expect(parseModalCommand('modal token set --token-id="ak-abc" --token-secret="as-def"'))
      .toEqual({ tokenId: "ak-abc", tokenSecret: "as-def" });
    expect(parseModalCommand("modal token set --token-id 'ak-abc' --token-secret 'as-def'"))
      .toEqual({ tokenId: "ak-abc", tokenSecret: "as-def" });
  });

  it("accepts a bare credential pair without flags", () => {
    expect(parseModalCommand("ak-abcXYZ as-defXYZ"))
      .toEqual({ tokenId: "ak-abcXYZ", tokenSecret: "as-defXYZ" });
  });

  it("ignores surrounding whitespace and prose", () => {
    expect(parseModalCommand("  run:  modal token set --token-id ak-example123 --token-secret as-example456  "))
      .toEqual({ tokenId: "ak-example123", tokenSecret: "as-example456" });
  });

  it("returns null when either part is missing", () => {
    expect(parseModalCommand("modal token set --token-id ak-only")).toBeNull();
    expect(parseModalCommand("modal token set --token-secret as-only")).toBeNull();
    expect(parseModalCommand("")).toBeNull();
    expect(parseModalCommand("   ")).toBeNull();
  });
});
