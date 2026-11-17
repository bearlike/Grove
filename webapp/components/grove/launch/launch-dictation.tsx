"use client";

import { Loader2Icon, MicIcon, SquareIcon, XIcon } from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { toast } from "sonner";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { ComposerSend } from "@/components/elements/composer";
import { DictationWaveform, useLevelHistory } from "@/components/grove/dictation-waveform";
import { appendTranscript } from "@/lib/grove/adapters/speech";
import { useWhoami } from "@/lib/grove/hooks";
import {
  useDictationControls,
  type DictationControls,
} from "@/lib/grove/runtime/speech";
import { LaunchTip } from "./launch-tip";

/**
 * The landing composer's dictation state, lifted so the composer can swap its
 * whole body for the voice view while the draft stays where it is.
 *
 * This route mounts no assistant-ui runtime, so it drives the same adapter the
 * workspace composer uses. The transcript is appended to the brief; `send`
 * then submits the brief the transcript landed in — the one case where the
 * reader does not press send themselves.
 */
export function useLaunchDictation(
  setPrompt: Dispatch<SetStateAction<string>>,
  submit: () => void,
): DictationControls & { readonly available: boolean; readonly send: () => void } {
  const available = useWhoami().data?.speech === true;
  const controls = useDictationControls(
    (transcript) => setPrompt((current) => appendTranscript(current, transcript)),
    (message) => toast.error(message),
  );
  // `submit` reads the draft from state, so it must run on the render AFTER
  // the transcript's `setPrompt` committed — hence a flag, not a direct call.
  const [sendNext, setSendNext] = useState(false);
  const latestSubmit = useRef(submit);
  useEffect(() => {
    latestSubmit.current = submit;
  });
  useEffect(() => {
    if (!sendNext || controls.phase !== "idle") return;
    setSendNext(false);
    latestSubmit.current();
  }, [sendNext, controls.phase]);
  const { done } = controls;
  const send = useCallback(() => {
    setSendNext(true);
    void done();
  }, [done]);
  return { ...controls, available, send };
}

/** The idle microphone; absent without a configured speech provider. */
export function LaunchDictateButton({
  available,
  onStart,
}: {
  readonly available: boolean;
  readonly onStart: () => void;
}): ReactNode {
  if (!available) return null;
  return (
    <TooltipIconButton
      type="button"
      tooltip="Dictate"
      side="top"
      aria-label="Start dictation"
      onClick={onStart}
      data-testid="launch-dictate"
    >
      <MicIcon aria-hidden />
    </TooltipIconButton>
  );
}

/**
 * Recording and transcribing, in the bar's full width: cancel on the left, the
 * live waveform across the middle, then stop (transcribe into the brief) and
 * send (transcribe and submit) on the right — the reference's four controls.
 */
export function LaunchVoiceView({
  phase,
  level,
  onCancel,
  onDone,
  onSend,
}: {
  readonly phase: "recording" | "transcribing";
  readonly level: () => number;
  readonly onCancel: () => void;
  readonly onDone: () => void;
  readonly onSend: () => void;
}): ReactNode {
  const recording = phase === "recording";
  const strip = useRef<HTMLDivElement | null>(null);
  const levels = useLevelHistory(recording, level, strip);
  return (
    <div className="flex items-center gap-2" data-slot="dictation" data-phase={recording ? "recording" : "transcribing"} data-testid="launch-voice">
      {/* Announced once per phase; a per-level readout would be read aloud forever. */}
      <span role="status" aria-live="polite" className="sr-only">
        {recording ? "Recording" : "Transcribing your recording"}
      </span>
      <TooltipIconButton
        type="button"
        tooltip="Cancel dictation"
        side="top"
        aria-label="Cancel dictation"
        onClick={onCancel}
        data-testid="launch-dictation-cancel"
      >
        <XIcon aria-hidden />
      </TooltipIconButton>
      <div
        ref={strip}
        className="min-w-0 flex-1"
        data-testid={recording ? "launch-dictation-recording" : "launch-dictation-transcribing"}
      >
        <DictationWaveform levels={levels} />
      </div>
      {recording ? (
        <>
          <TooltipIconButton
            type="button"
            tooltip="Stop and transcribe"
            side="top"
            aria-label="Finish dictation"
            onClick={onDone}
            data-testid="launch-dictation-done"
          >
            <SquareIcon className="fill-current" aria-hidden />
          </TooltipIconButton>
          <LaunchTip label="Send dictated message">
            <ComposerSend
              streaming={false}
              idle={false}
              aria-label="Send dictated message"
              onClick={onSend}
              data-testid="launch-dictation-send"
            />
          </LaunchTip>
        </>
      ) : (
        <span aria-busy className="inline-flex size-8 items-center justify-center">
          <Loader2Icon className="animate-spin" aria-hidden />
        </span>
      )}
    </div>
  );
}
