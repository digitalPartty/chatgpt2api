"use client";
import { ArrowUp, Check, ChevronDown, ImagePlus, LoaderCircle, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ClipboardEvent, type RefObject } from "react";

import { ImageLightbox } from "@/components/image-lightbox";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type ImageComposerProps = {
  prompt: string;
  imageCount: string;
  imageSize: string;
  availableQuota: string;
  activeTaskCount: number;
  referenceImages: Array<{ name: string; dataUrl: string }>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onPromptChange: (value: string) => void;
  onImageCountChange: (value: string) => void;
  onImageSizeChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
  onPickReferenceImage: () => void;
  onReferenceImageChange: (files: File[]) => void | Promise<void>;
  onRemoveReferenceImage: (index: number) => void;
};

export function ImageComposer({
  prompt,
  imageCount,
  imageSize,
  availableQuota,
  activeTaskCount,
  referenceImages,
  textareaRef,
  fileInputRef,
  onPromptChange,
  onImageCountChange,
  onImageSizeChange,
  onSubmit,
  onPickReferenceImage,
  onReferenceImageChange,
  onRemoveReferenceImage,
}: ImageComposerProps) {
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [isSizeMenuOpen, setIsSizeMenuOpen] = useState(false);
  const [sizeMenuPos, setSizeMenuPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });
  const sizeMenuRef = useRef<HTMLDivElement>(null);
  const sizeMenuBtnRef = useRef<HTMLButtonElement>(null);
  const lightboxImages = useMemo(
    () => referenceImages.map((image, index) => ({ id: `${image.name}-${index}`, src: image.dataUrl })),
    [referenceImages],
  );
  const imageSizeOptions = [
    { value: "", label: "未指定" },
    { value: "1:1", label: "1:1 (正方形)" },
    { value: "16:9", label: "16:9 (横版)" },
    { value: "4:3", label: "4:3 (横版)" },
    { value: "3:4", label: "3:4 (竖版)" },
    { value: "9:16", label: "9:16 (竖版)" },
  ];
  const imageSizeLabel = imageSizeOptions.find((option) => option.value === imageSize)?.label || "未指定";

  useEffect(() => {
    if (!isSizeMenuOpen) {
      return;
    }
    const handlePointerDown = (event: MouseEvent) => {
      if (!sizeMenuRef.current?.contains(event.target as Node)) {
        setIsSizeMenuOpen(false);
      }
    };
    window.addEventListener("mousedown", handlePointerDown);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
    };
  }, [isSizeMenuOpen]);

  const handleTextareaPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const imageFiles = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith("image/"));
    if (imageFiles.length === 0) {
      return;
    }

    event.preventDefault();
    void onReferenceImageChange(imageFiles);
  };

  return (
    <div className="shrink-0 flex justify-center px-1 sm:px-0">
      <div style={{ width: "min(980px, 100%)" }}>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(event) => {
            void onReferenceImageChange(Array.from(event.target.files || []));
          }}
        />

        {referenceImages.length > 0 ? (
          <div className="mb-2 flex gap-2 overflow-x-auto px-1 pb-1 sm:mb-3 sm:flex-wrap sm:overflow-visible sm:pb-0">
            {referenceImages.map((image, index) => (
              <div key={`${image.name}-${index}`} className="relative size-14 shrink-0 sm:size-16">
                <button
                  type="button"
                  onClick={() => {
                    setLightboxIndex(index);
                    setLightboxOpen(true);
                  }}
                  className="group size-14 overflow-hidden rounded-2xl border border-white/[0.1] bg-white/[0.05] transition hover:border-white/20 sm:size-16"
                  aria-label={`预览参考图 ${image.name || index + 1}`}
                >
                  <img
                    src={image.dataUrl}
                    alt={image.name || `参考图 ${index + 1}`}
                    className="h-full w-full object-cover"
                  />
                </button>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onRemoveReferenceImage(index);
                  }}
                  className="absolute -right-1 -top-1 inline-flex size-5 items-center justify-center rounded-full border border-white/[0.1] bg-[#26272c] text-white/50 transition hover:border-white/20 hover:text-white"
                  aria-label={`移除参考图 ${image.name || index + 1}`}
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
          </div>
        ) : null}

        <div className="overflow-hidden rounded-[24px] border border-white/[0.08] bg-[#26272c] shadow-[0_14px_60px_-42px_rgba(0,0,0,0.6)] sm:rounded-[32px] sm:shadow-none">
          <div
            className="relative cursor-text"
            onClick={() => {
              textareaRef.current?.focus();
            }}
          >
            <ImageLightbox
              images={lightboxImages}
              currentIndex={lightboxIndex}
              open={lightboxOpen}
              onOpenChange={setLightboxOpen}
              onIndexChange={setLightboxIndex}
            />
            <Textarea
              ref={textareaRef}
              value={prompt}
              onChange={(event) => onPromptChange(event.target.value)}
              onPaste={handleTextareaPaste}
              placeholder={
                referenceImages.length > 0
                  ? "描述你希望如何修改参考图"
                  : "输入你想要生成的画面，也可直接粘贴图片"
              }
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void onSubmit();
                }
              }}
              className="min-h-[82px] resize-none rounded-[24px] border-0 bg-transparent px-4 pt-4 pb-2 text-[15px] leading-6 text-white shadow-none placeholder:text-white/30 focus-visible:ring-0 sm:min-h-[148px] sm:rounded-[32px] sm:px-6 sm:pt-6 sm:pb-20 sm:leading-7"
            />

            <div className="rounded-b-[24px] border-t border-white/[0.06] bg-[#26272c] px-3 pb-3 pt-2 sm:absolute sm:inset-x-0 sm:bottom-0 sm:rounded-b-none sm:border-t-0 sm:bg-gradient-to-t sm:from-[#26272c] sm:via-[#26272c]/95 sm:to-transparent sm:px-6 sm:pb-4 sm:pt-6" onClick={(event) => event.stopPropagation()}>
              <div className="flex items-end justify-between gap-2 sm:gap-3">
                <div className="hide-scrollbar flex min-w-0 flex-1 flex-nowrap items-center gap-1.5 overflow-x-auto pb-0.5 sm:flex-wrap sm:gap-3 sm:overflow-visible sm:pb-0">
                  <Button
                    type="button"
                    variant="outline"
                    className="h-9 shrink-0 rounded-full border-white/[0.1] bg-white/[0.06] px-3 text-xs font-medium text-white/70 shadow-none hover:bg-white/[0.1] hover:text-white sm:h-10 sm:px-4 sm:text-sm"
                    onClick={onPickReferenceImage}
                    aria-label={referenceImages.length > 0 ? "添加参考图" : "上传"}
                  >
                    <ImagePlus className="size-3.5 sm:size-4" />
                    <span className="hidden sm:inline">{referenceImages.length > 0 ? "添加参考图" : "上传"}</span>
                  </Button>
                  <div className="shrink-0 rounded-full bg-white/[0.06] px-2 py-1 text-[10px] font-medium text-white/45 sm:px-3 sm:py-2 sm:text-xs">
                    <span className="hidden sm:inline">剩余额度 </span>{availableQuota}
                  </div>
                  {activeTaskCount > 0 && (
                    <div className="flex shrink-0 items-center gap-1 rounded-full bg-amber-500/15 px-2 py-1 text-[10px] font-medium text-amber-400 sm:gap-1.5 sm:px-3 sm:py-2 sm:text-xs">
                      <LoaderCircle className="size-3 animate-spin" />
                      {activeTaskCount}<span className="hidden sm:inline"> 个任务处理中</span>
                    </div>
                  )}
                  <div className="flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-white/[0.1] bg-white/[0.06] px-2 py-0.5 sm:h-auto sm:gap-2 sm:px-3 sm:py-1">
                    <span className="hidden text-[11px] font-medium text-white/60 sm:inline sm:text-sm">张数</span>
                    <Input
                      type="number"
                      inputMode="numeric"
                      min="1"
                      max="100"
                      step="1"
                      value={imageCount}
                      onChange={(event) => onImageCountChange(event.target.value)}
                      className="h-7 w-[40px] border-0 bg-transparent px-0 text-center text-xs font-medium text-white/80 shadow-none focus-visible:ring-0 sm:h-8 sm:w-[64px] sm:text-sm"
                    />
                  </div>
                  <div
                    className="relative flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-white/[0.1] bg-white/[0.06] px-2 py-0.5 text-[11px] sm:h-auto sm:gap-2 sm:px-3 sm:py-1 sm:text-[13px]"
                  >
                    <span className="hidden font-medium text-white/60 sm:inline sm:text-sm">比例</span>
                    <button
                      ref={sizeMenuBtnRef}
                      type="button"
                      className="flex h-7 w-[78px] items-center justify-between bg-transparent text-left text-xs font-bold text-white/80 min-[390px]:w-[96px] sm:h-8 sm:w-[132px]"
                      onClick={() => {
                        if (!isSizeMenuOpen && sizeMenuBtnRef.current) {
                          const rect = sizeMenuBtnRef.current.getBoundingClientRect();
                          const menuWidth = Math.min(186, window.innerWidth - 32);
                          setSizeMenuPos({ top: rect.top - 8, left: Math.max(16, Math.min(rect.left, window.innerWidth - menuWidth - 16)) });
                        }
                        setIsSizeMenuOpen((open) => !open);
                      }}
                    >
                      <span className="truncate">{imageSizeLabel}</span>
                      <ChevronDown className={cn("size-4 shrink-0 opacity-60 transition", isSizeMenuOpen && "rotate-180")} />
                    </button>
                    {isSizeMenuOpen ? (
                      <div
                        ref={sizeMenuRef}
                        className="fixed z-[80] max-h-[45dvh] overflow-y-auto rounded-3xl border border-white/[0.08] bg-[#2e2f35] p-2 shadow-[0_24px_80px_-32px_rgba(0,0,0,0.6)]"
                        style={{
                          top: sizeMenuPos.top,
                          left: sizeMenuPos.left,
                          transform: "translateY(-100%)",
                          width: "min(186px, calc(100vw - 2rem))",
                        }}
                      >
                        {imageSizeOptions.map((option) => {
                          const active = option.value === imageSize;
                          return (
                            <button
                              key={option.label}
                              type="button"
                              className={cn(
                                "flex w-full items-center justify-between rounded-2xl px-3 py-2 text-left text-sm text-white/65 transition hover:bg-white/[0.08] hover:text-white",
                                active && "bg-white/[0.1] font-medium text-white",
                              )}
                              onClick={() => {
                                onImageSizeChange(option.value);
                                setIsSizeMenuOpen(false);
                              }}
                            >
                              <span>{option.label}</span>
                              {active ? <Check className="size-4" /> : null}
                            </button>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>

                </div>

                <button
                  type="button"
                  onClick={() => void onSubmit()}
                  disabled={!prompt.trim()}
                  className="inline-flex size-10 shrink-0 items-center justify-center rounded-full bg-white text-stone-950 transition hover:bg-white/90 disabled:cursor-not-allowed disabled:bg-white/20 disabled:text-white/40 sm:size-11"
                  aria-label={referenceImages.length > 0 ? "编辑图片" : "生成图片"}
                >
                  <ArrowUp className="size-3.5 sm:size-4" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

