"""Incremental <utterance> extractor for streaming TTS.

COPIED VERBATIM (behaviour-preserving) from the protected mock-oral system:
  perplexity-mock-oral/orchestrator/livekit_agent.py  L208-296  (_UtteranceStreamer)

Why this is copied rather than rewritten: it already solves the one failure
this project cannot tolerate -- a partial closing tag leaking into spoken
audio when "</utter" and "ance>" arrive in different stream deltas. It has
been validated in live exams. Do not "simplify" it.

The only change is the class name and the docstring.
"""

from __future__ import annotations

import re


class UtteranceStreamer:
    """Extract <utterance> content from a streamed LLM response and release it
    in sentence-sized chunks for TTS.

    The output contract puts <utterance> first and the hidden <state> block
    after it, so speech can begin while the model is still writing its
    bookkeeping. Tags may arrive split across stream deltas, so the parser
    holds back a small tail and releases text only at sentence boundaries.
    """

    _OPEN = "<utterance>"
    _CLOSE = "</utterance>"
    _BOUNDARY = re.compile(r"[.!?]['\")\]]?\s")

    def __init__(self) -> None:
        self._pre = ""            # text seen before the opening tag
        self._buf = ""            # unspoken utterance content
        self._spoken: list[str] = []
        self._in_utt = False
        self._closed = False

    def feed(self, delta: str) -> str:
        """Feed one stream delta; return any text now safe to speak."""
        if self._closed or not delta:
            return ""
        if not self._in_utt:
            self._pre += delta
            i = self._pre.find(self._OPEN)
            if i < 0:
                # Keep just enough tail to detect a tag split across deltas.
                self._pre = self._pre[-(len(self._OPEN) - 1):]
                return ""
            self._in_utt = True
            self._buf = self._pre[i + len(self._OPEN):]
            self._pre = ""
        else:
            self._buf += delta

        j = self._buf.find(self._CLOSE)
        if j >= 0:
            chunk = self._buf[:j].strip()
            self._buf = ""
            self._closed = True
            if chunk:
                self._spoken.append(chunk)
                return chunk + " "
            return ""

        # Hold back a tail so a split closing tag is never spoken.
        safe_len = len(self._buf) - (len(self._CLOSE) - 1)
        if safe_len <= 0:
            return ""
        safe = self._buf[:safe_len]
        last = None
        for m in self._BOUNDARY.finditer(safe):
            last = m
        if last is None:
            return ""
        cut = last.end()
        chunk = self._buf[:cut]
        self._buf = self._buf[cut:]
        self._spoken.append(chunk)
        return chunk

    def flush(self) -> str:
        """Stream ended (possibly with a malformed/unclosed tag): emit the rest."""
        if self._closed or not self._in_utt:
            return ""
        chunk = self._buf
        k = chunk.find("<")
        if k >= 0:
            chunk = chunk[:k]
        self._buf = ""
        self._closed = True
        chunk = chunk.strip()
        if chunk:
            self._spoken.append(chunk)
        return chunk

    def spoken_text(self) -> str:
        """Everything released for speech this turn (for logs and guards)."""
        return "".join(self._spoken).strip()
