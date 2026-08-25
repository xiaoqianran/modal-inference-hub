import { useCallback, useEffect, useRef, useState } from "react";

export function useObjectUrl() {
  const [url, setUrl] = useState<string | null>(null);
  const currentUrl = useRef<string | null>(null);

  const replace = useCallback((blob: Blob | null) => {
    const nextUrl = blob ? URL.createObjectURL(blob) : null;
    const previousUrl = currentUrl.current;
    currentUrl.current = nextUrl;
    setUrl(nextUrl);
    if (previousUrl) URL.revokeObjectURL(previousUrl);
  }, []);

  useEffect(() => () => {
    if (currentUrl.current) URL.revokeObjectURL(currentUrl.current);
  }, []);

  return [url, replace] as const;
}
