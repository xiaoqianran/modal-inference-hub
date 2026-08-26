import { describe, expect, it } from "vitest";
import { thumbnailCoverRect } from "./useProjectThumbnails";

describe("thumbnailCoverRect", () => {
  it("center-crops landscape images", () => {
    expect(thumbnailCoverRect(200, 100, 100)).toEqual({
      x: -50,
      y: 0,
      width: 200,
      height: 100,
    });
  });

  it("center-crops portrait images", () => {
    expect(thumbnailCoverRect(100, 200, 100)).toEqual({
      x: 0,
      y: -50,
      width: 100,
      height: 200,
    });
  });

  it("keeps square images aligned", () => {
    expect(thumbnailCoverRect(256, 256, 96)).toEqual({
      x: 0,
      y: 0,
      width: 96,
      height: 96,
    });
  });
});
