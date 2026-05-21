"use client";

import { useEffect, useMemo, useState } from "react";

import { cn } from "@/lib/utils";

type ImageThumbnailProps = {
  src: string;
  thumbnailSrc?: string;
  alt?: string;
  className?: string;
  imageClassName?: string;
};

// Normalize URLs that point to local image paths to use the current page's origin.
// This fixes mixed-content issues when images were generated with http:// but the
// page is served over https:// (common behind a reverse proxy without CHATGPT2API_BASE_URL set).
export function normalizeImageUrl(url: string): string {
  if (!url || typeof window === "undefined") return url;
  try {
    const parsed = new URL(url);
    if (parsed.pathname.startsWith("/images/") || parsed.pathname.startsWith("/image-thumbnails/")) {
      return `${window.location.origin}${parsed.pathname}${parsed.search}`;
    }
  } catch {
    // relative or invalid URL, return as-is
  }
  return url;
}

export function getImageThumbnailUrl(src: string) {
  const normalized = normalizeImageUrl(src);
  const marker = "/images/";
  const index = normalized.indexOf(marker);
  if (index < 0) return normalized;
  return `${normalized.slice(0, index)}/image-thumbnails/${normalized.slice(index + marker.length)}`;
}

export function ImageThumbnail({ src, thumbnailSrc, alt = "", className, imageClassName }: ImageThumbnailProps) {
  const initialSrc = useMemo(() => thumbnailSrc ? normalizeImageUrl(thumbnailSrc) : getImageThumbnailUrl(src), [src, thumbnailSrc]);
  const [currentSrc, setCurrentSrc] = useState(initialSrc);
  const fullSrc = useMemo(() => normalizeImageUrl(src), [src]);

  useEffect(() => {
    setCurrentSrc(initialSrc);
  }, [initialSrc]);

  return (
    <span className={cn("block overflow-hidden bg-stone-100", className)}>
      <img
        src={currentSrc}
        alt={alt}
        className={cn("h-full w-full object-cover", imageClassName)}
        loading="lazy"
        decoding="async"
        onError={() => {
          if (currentSrc !== fullSrc) {
            setCurrentSrc(fullSrc);
          }
        }}
      />
    </span>
  );
}
