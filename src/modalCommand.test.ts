import { describe, expect, it } from "vitest";
import { parseModalCommand } from "./modalCommand";

describe("parseModalCommand", () => {
  it("parses a standard modal token set command", () => {
    expect(parseModalCommand(
      "modal token set --token-id ak-rUjxOsykqXQEealjoNqTt1 --token-secret as-h205JkHPq4BsEsUldGPiIG",
    )).toEqual({ tokenId: "ak-rUjxOsykqXQEealjoNqTt1", tokenSecret: "as-h205JkHPq4BsEsUldGPiIG" });
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
    expect(parseModalCommand("  run:  modal token set --token-id ak-x1 --token-secret as-y2  "))
      .toEqual({ tokenId: "ak-x1", tokenSecret: "as-y2" });
  });

  it("returns null when either part is missing", () => {
    expect(parseModalCommand("modal token set --token-id ak-only")).toBeNull();
    expect(parseModalCommand("modal token set --token-secret as-only")).toBeNull();
    expect(parseModalCommand("")).toBeNull();
    expect(parseModalCommand("   ")).toBeNull();
  });
});
