"use client";
import { AtSign, ArrowUp, Check, ChevronDown, ImagePlus, LoaderCircle, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent, type RefObject } from "react";

import { ImageLightbox } from "@/components/image-lightbox";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export type AtMentionImage = {
  id: string;
  name: string;
  dataUrl?: string;
  src?: string;
};

type ImageComposerProps = {
  prompt: string;
  imageCount: string;
  imageSize: string;
  imageQuality: string;
  availableQuota: string;
  activeTaskCount: number;
  referenceImages: Array<{ name: string; dataUrl: string }>;
  availableImages?: AtMentionImage[];
  textareaRef: RefObject<HTMLDivElement | null>;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onPromptChange: (value: string) => void;
  onImageCountChange: (value: string) => void;
  onImageSizeChange: (value: string) => void;
  onImageQualityChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
  onPickReferenceImage: () => void;
  onReferenceImageChange: (files: File[]) => void | Promise<void>;
  onRemoveReferenceImage: (index: number) => void;
  onAtMentionSelect?: (image: AtMentionImage) => void;
};

function getEditorText(el: HTMLElement): string {
  let text = "";
  for (const node of el.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent ?? "";
    } else if ((node as HTMLElement).dataset?.mention !== undefined) {
      text += node.textContent ?? "";
    } else if (node.nodeName === "BR") {
      text += "\n";
    } else if (node.nodeName === "DIV") {
      text += "\n" + getEditorText(node as HTMLElement);
    }
  }
  return text;
}

function getCursorOffset(el: HTMLElement): number {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return 0;
  const range = sel.getRangeAt(0);
  const pre = range.cloneRange();
  pre.selectNodeContents(el);
  pre.setEnd(range.endContainer, range.endOffset);
  return pre.toString().length;
}

export function ImageComposer({
  prompt,
  imageCount,
  imageSize,
  imageQuality,
  availableQuota,
  activeTaskCount,
  referenceImages,
  availableImages = [],
  textareaRef,
  fileInputRef,
  onPromptChange,
  onImageCountChange,
  onImageSizeChange,
  onImageQualityChange,
  onSubmit,
  onPickReferenceImage,
  onReferenceImageChange,
  onRemoveReferenceImage,
  onAtMentionSelect,
}: ImageComposerProps) {
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [isSizeMenuOpen, setIsSizeMenuOpen] = useState(false);
  const [sizeMenuPos, setSizeMenuPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });
  const [isQualityMenuOpen, setIsQualityMenuOpen] = useState(false);
  const [qualityMenuPos, setQualityMenuPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });
  const [atMentionOpen, setAtMentionOpen] = useState(false);
  const [atMentionStart, setAtMentionStart] = useState(-1);
  const [atMentionQuery, setAtMentionQuery] = useState("");
  const sizeMenuRef = useRef<HTMLDivElement>(null);
  const sizeMenuBtnRef = useRef<HTMLButtonElement>(null);
  const qualityMenuRef = useRef<HTMLDivElement>(null);
  const qualityMenuBtnRef = useRef<HTMLButtonElement>(null);
  const atMentionRef = useRef<HTMLDivElement>(null);
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
  const imageQualityOptions = [
    { value: "1k", label: "1K" },
    { value: "2k", label: "2K" },
    { value: "4k", label: "4K" },
  ];
  const imageQualityLabel = imageQualityOptions.find((option) => option.value === imageQuality)?.label || "1K";

  useEffect(() => {
    if (!isSizeMenuOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (!sizeMenuRef.current?.contains(event.target as Node)) {
        setIsSizeMenuOpen(false);
      }
    };
    window.addEventListener("mousedown", handlePointerDown);
    return () => window.removeEventListener("mousedown", handlePointerDown);
  }, [isSizeMenuOpen]);

  useEffect(() => {
    if (!isQualityMenuOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (!qualityMenuRef.current?.contains(event.target as Node)) {
        setIsQualityMenuOpen(false);
      }
    };
    window.addEventListener("mousedown", handlePointerDown);
    return () => window.removeEventListener("mousedown", handlePointerDown);
  }, [isQualityMenuOpen]);

  useEffect(() => {
    if (!atMentionOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (!atMentionRef.current?.contains(event.target as Node)) {
        setAtMentionOpen(false);
      }
    };
    window.addEventListener("mousedown", handlePointerDown);
    return () => window.removeEventListener("mousedown", handlePointerDown);
  }, [atMentionOpen]);

  // Sync external prompt clears to the DOM
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    if (prompt === "") {
      el.innerHTML = "";
    }
  }, [prompt, textareaRef]);

  const filteredAtImages = useMemo(() => {
    if (!atMentionQuery) return availableImages;
    const q = atMentionQuery.toLowerCase();
    return availableImages.filter((img) => img.name.toLowerCase().includes(q));
  }, [availableImages, atMentionQuery]);

  const detectAtMention = useCallback((value: string, cursorPos: number) => {
    const textBeforeCursor = value.slice(0, cursorPos);
    const lastAtIndex = textBeforeCursor.lastIndexOf("@");
    if (lastAtIndex !== -1) {
      const textAfterAt = textBeforeCursor.slice(lastAtIndex + 1);
      if (!textAfterAt.includes(" ") && !textAfterAt.includes("\n")) {
        setAtMentionOpen(true);
        setAtMentionStart(lastAtIndex);
        setAtMentionQuery(textAfterAt);
        return;
      }
    }
    setAtMentionOpen(false);
  }, []);

  const handleEditorInput = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    const text = getEditorText(el);
    detectAtMention(text, getCursorOffset(el));
    onPromptChange(text);
  }, [textareaRef, detectAtMention, onPromptChange]);

  const handleAtMentionSelect = useCallback(
    (image: AtMentionImage) => {
      const el = textareaRef.current;
      if (!el) return;

      const chip = document.createElement("span");
      chip.contentEditable = "false";
      chip.dataset.mention = image.id;
      chip.textContent = `@${image.name}`;
      chip.className =
        "inline-flex items-center rounded-full bg-blue-500/20 px-1.5 text-blue-300 select-none";

      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let offset = 0;
      let targetNode: Text | null = null;
      let targetOffset = 0;
      while (walker.nextNode()) {
        const node = walker.currentNode as Text;
        const len = node.textContent?.length ?? 0;
        if (offset + len > atMentionStart) {
          targetNode = node;
          targetOffset = atMentionStart - offset;
          break;
        }
        offset += len;
      }

      if (targetNode) {
        const range = document.createRange();
        range.setStart(targetNode, targetOffset);
        range.setEnd(targetNode, Math.min(targetOffset + 1 + atMentionQuery.length, targetNode.length));
        range.deleteContents();
        range.insertNode(chip);
        const after = document.createRange();
        after.setStartAfter(chip);
        after.collapse(true);
        window.getSelection()?.removeAllRanges();
        window.getSelection()?.addRange(after);
      }

      onPromptChange(getEditorText(el));
      onAtMentionSelect?.(image);
      setAtMentionOpen(false);
      el.focus();
    },
    [atMentionStart, atMentionQuery, onPromptChange, onAtMentionSelect, textareaRef],
  );

  const handleAtButtonClick = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.focus();
    const offset = getCursorOffset(el);
    document.execCommand("insertText", false, "@");
    setAtMentionStart(offset);
    setAtMentionQuery("");
    setAtMentionOpen(true);
  }, [textareaRef]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "Escape" && atMentionOpen) {
        e.preventDefault();
        setAtMentionOpen(false);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void onSubmit();
        return;
      }
      if (e.key === "Backspace") {
        const sel = window.getSelection();
        if (!sel || sel.rangeCount === 0) return;
        const range = sel.getRangeAt(0);
        if (!range.collapsed) return;
        if (range.startOffset === 0) {
          const prev = range.startContainer.previousSibling as HTMLElement | null;
          if (prev?.dataset?.mention !== undefined) {
            e.preventDefault();
            prev.remove();
            onPromptChange(getEditorText(textareaRef.current!));
          }
        }
      }
    },
    [atMentionOpen, onSubmit, textareaRef, onPromptChange],
  );

  const handleEditorPaste = useCallback(
    (e: ClipboardEvent<HTMLDivElement>) => {
      const imageFiles = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith("image/"));
      if (imageFiles.length > 0) {
        e.preventDefault();
        void onReferenceImageChange(imageFiles);
        return;
      }
      const text = e.clipboardData.getData("text/plain");
      if (text) {
        e.preventDefault();
        document.execCommand("insertText", false, text);
      }
    },
    [onReferenceImageChange],
  );

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

        <div className="relative">
          {atMentionOpen && (
            <div
              ref={atMentionRef}
              className="absolute bottom-full left-0 right-0 z-50 mb-2 overflow-hidden rounded-[20px] border border-white/[0.08] bg-[#2e2f35] p-3 shadow-[0_24px_80px_-32px_rgba(0,0,0,0.6)]"
            >
              <div className="mb-2 text-[11px] font-medium text-white/40">选择参考图</div>
              {filteredAtImages.length > 0 ? (
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {filteredAtImages.map((img) => (
                    <button
                      key={img.id}
                      type="button"
                      onClick={() => handleAtMentionSelect(img)}
                      className="flex shrink-0 flex-col items-center gap-1 rounded-xl p-1 transition hover:bg-white/[0.06]"
                    >
                      <div className="size-14 overflow-hidden rounded-xl border border-white/[0.1] bg-white/[0.05]">
                        <img src={img.dataUrl ?? img.src ?? ""} alt={img.name} className="h-full w-full object-cover" />
                      </div>
                      <span className="w-14 truncate text-center text-[10px] text-white/50">{img.name}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="py-2 text-center text-xs text-white/30">暂无上传图片</div>
              )}
            </div>
          )}

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
              <div
                ref={textareaRef}
                contentEditable
                suppressContentEditableWarning
                onInput={handleEditorInput}
                onKeyDown={handleKeyDown}
                onPaste={handleEditorPaste}
                data-placeholder={
                  referenceImages.length > 0
                    ? "描述你希望如何修改参考图"
                    : "输入你想要生成的画面，也可直接粘贴图片"
                }
                className="mention-editor min-h-[82px] w-full rounded-[24px] border-0 bg-transparent px-4 pt-4 pb-2 text-[15px] leading-6 text-white outline-none sm:min-h-[148px] sm:rounded-[32px] sm:px-6 sm:pt-6 sm:pb-20 sm:leading-7"
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
                    {availableImages.length > 0 && (
                      <Button
                        type="button"
                        variant="outline"
                        className="h-9 shrink-0 rounded-full border-white/[0.1] bg-white/[0.06] px-3 text-xs font-medium text-white/70 shadow-none hover:bg-white/[0.1] hover:text-white sm:h-10 sm:px-4 sm:text-sm"
                        onClick={handleAtButtonClick}
                        aria-label="引用上传图片"
                      >
                        <AtSign className="size-3.5 sm:size-4" />
                        <span className="hidden sm:inline">引用图片</span>
                      </Button>
                    )}
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
                    <div
                      className="relative flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-white/[0.1] bg-white/[0.06] px-2 py-0.5 text-[11px] sm:h-auto sm:gap-2 sm:px-3 sm:py-1 sm:text-[13px]"
                    >
                      <span className="hidden font-medium text-white/60 sm:inline sm:text-sm">清晰度</span>
                      <button
                        ref={qualityMenuBtnRef}
                        type="button"
                        className="flex h-7 w-[48px] items-center justify-between bg-transparent text-left text-xs font-bold text-white/80 sm:h-8 sm:w-[64px]"
                        onClick={() => {
                          if (!isQualityMenuOpen && qualityMenuBtnRef.current) {
                            const rect = qualityMenuBtnRef.current.getBoundingClientRect();
                            const menuWidth = Math.min(140, window.innerWidth - 32);
                            setQualityMenuPos({ top: rect.top - 8, left: Math.max(16, Math.min(rect.left, window.innerWidth - menuWidth - 16)) });
                          }
                          setIsQualityMenuOpen((open) => !open);
                        }}
                      >
                        <span className="truncate">{imageQualityLabel}</span>
                        <ChevronDown className={cn("size-4 shrink-0 opacity-60 transition", isQualityMenuOpen && "rotate-180")} />
                      </button>
                      {isQualityMenuOpen ? (
                        <div
                          ref={qualityMenuRef}
                          className="fixed z-[80] max-h-[45dvh] overflow-y-auto rounded-3xl border border-white/[0.08] bg-[#2e2f35] p-2 shadow-[0_24px_80px_-32px_rgba(0,0,0,0.6)]"
                          style={{
                            top: qualityMenuPos.top,
                            left: qualityMenuPos.left,
                            transform: "translateY(-100%)",
                            width: "min(140px, calc(100vw - 2rem))",
                          }}
                        >
                          {imageQualityOptions.map((option) => {
                            const active = option.value === imageQuality;
                            return (
                              <button
                                key={option.label}
                                type="button"
                                className={cn(
                                  "flex w-full items-center justify-between rounded-2xl px-3 py-2 text-left text-sm text-white/65 transition hover:bg-white/[0.08] hover:text-white",
                                  active && "bg-white/[0.1] font-medium text-white",
                                )}
                                onClick={() => {
                                  onImageQualityChange(option.value);
                                  setIsQualityMenuOpen(false);
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
    </div>
  );
}