import { useEffect, useState } from "react";
import { projectSourceBlob, type AgentInfo, type Project } from "../agent";

const THUMBNAIL_SIZE = 96;
const MAX_THUMBNAILS = 12;

export function thumbnailCoverRect(width: number, height: number, size = THUMBNAIL_SIZE) {
  const scale = Math.max(size / width, size / height);
  const drawWidth = width * scale;
  const drawHeight = height * scale;
  return {
    x: (size - drawWidth) / 2,
    y: (size - drawHeight) / 2,
    width: drawWidth,
    height: drawHeight,
  };
}

async function compactThumbnail(source: Blob): Promise<Blob> {
  if (typeof createImageBitmap !== "function") return source;
  const bitmap = await createImageBitmap(source);
  try {
    const rect = thumbnailCoverRect(bitmap.width, bitmap.height);
    const canvas = document.createElement("canvas");
    canvas.width = THUMBNAIL_SIZE;
    canvas.height = THUMBNAIL_SIZE;
    const context = canvas.getContext("2d");
    if (!context) return source;
    context.drawImage(
      bitmap,
      rect.x,
      rect.y,
      rect.width,
      rect.height,
    );
    return await new Promise<Blob>((resolve) => {
      canvas.toBlob((blob) => resolve(blob ?? source), "image/webp", 0.76);
    });
  } finally {
    bitmap.close();
  }
}

export function useProjectThumbnails(agent: AgentInfo | null, projects: Project[]) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const visibleProjects = projects.slice(0, MAX_THUMBNAILS);
  const projectKey = visibleProjects.map((project) => project.id).join("|");

  useEffect(() => {
    let disposed = false;
    const objectUrls: string[] = [];
    setUrls({});
    if (!agent?.running) return () => undefined;

    for (const project of visibleProjects) {
      void projectSourceBlob(agent, project.id)
        .then(compactThumbnail)
        .then((blob) => {
          if (disposed) return;
          const url = URL.createObjectURL(blob);
          objectUrls.push(url);
          setUrls((current) => ({ ...current, [project.id]: url }));
        })
        .catch(() => {
          // Thumbnail loading is decorative; project navigation remains usable.
        });
    }

    return () => {
      disposed = true;
      objectUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [agent?.port, agent?.running, projectKey]);

  return urls;
}
