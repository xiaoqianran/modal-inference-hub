import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CanonicalAsset, ModelProfile, ModelSpec, Project } from "../agent";
import GenerationReviewDialog from "./GenerationReviewDialog";

const project = {
  id: "project-1",
  title: "shoe.png",
  source_name: "shoe.png",
  source_bytes: 1_000_000,
  source: {
    id: "source-1",
    role: "source-image",
    mime: "image/png",
    bytes: 1_000_000,
    sha256: "b".repeat(64),
    width: 1024,
    height: 1024,
  },
  canonical_id: "canonical-1",
  canonical_sha256: "a".repeat(64),
  canonical_bytes: 524_288,
  model: null,
  profile: null,
  job_id: null,
  artifact_id: null,
  artifact_sha256: null,
  artifact_bytes: null,
  artifact_canonical_sha256: null,
  status: "ready",
  error: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
} satisfies Project;

const canonical: CanonicalAsset = {
  id: "canonical-1",
  role: "canonical-rgba",
  mime: "image/png",
  bytes: 524_288,
  sha256: "a".repeat(64),
};

const profile: ModelProfile = { id: "recommended", name: "推荐" };
const model: ModelSpec = {
  id: "pixal3d",
  name: "Pixal3D",
  description: "Textured reconstruction",
  status: "enabled",
  output: "textured",
  warm_seconds: 8.5,
  profiles: [profile],
};

describe("GenerationReviewDialog", () => {
  it("renders only real generation inputs before submit", () => {
    const html = renderToStaticMarkup(
      <GenerationReviewDialog
        open
        project={project}
        canonical={canonical}
        model={model}
        profile={profile}
        selectedComponents={2}
        componentCount={3}
        busy={false}
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />,
    );

    expect(html).toContain("Pixal3D");
    expect(html).toContain("推荐");
    expect(html).toContain("2/3 个物体");
    expect(html).toContain("512 KiB");
    expect(html).toContain("仅标准化前景");
    expect(html).not.toContain("$");
    expect(html).not.toContain("%");
  });

  it("renders nothing while closed", () => {
    expect(renderToStaticMarkup(
      <GenerationReviewDialog
        open={false}
        project={project}
        canonical={canonical}
        model={model}
        profile={profile}
        selectedComponents={2}
        componentCount={3}
        busy={false}
        onCancel={() => undefined}
        onConfirm={() => undefined}
      />,
    )).toBe("");
  });
});
