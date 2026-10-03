# Launch copy

Attach `launch/musiccontext-launch.mp4` (1080p, 32 s, 10 MB, has sound; the text carries it muted) to post 1 on X and
to the LinkedIn post. `launch/thumbnail.png` is the poster frame. Every number below comes from the run that scored
the film. Beat positions are the analyzer's own estimate, and the copy says so.

---

## X thread

**1/**
AI agents can generate remarkable video. They still choose the music by vibe.

I built MusicContext. It plans a soundtrack from the video's actual structure, and tells you which numbers it measured and which it guessed.

This film was scored by it.

**2/**
The musical decisions that make an edit work are temporal:

- the hit lands on the reveal, not 900 ms later
- the arrangement thins out under narration
- cuts fall on beats
- the music resolves instead of getting cut off

A "cinematic" tag decides none of that.

**3/**
video → story → music direction → soundtrack → sync

Analysis is deterministic FFmpeg + numpy. No vision model, no OCR. It plans, finds music in your own library or generates it offline, then mixes it: trim, loop, fade, duck under speech, align to beats.

**4/**
Every value says how it was obtained.

[user] you told it
[detected] it measured it
[inferred] a judgement
[estimated] a heuristic

An agent can pass that on instead of false confidence. And no track is "licensed" unless a sidecar or provider says so.

**5/**
How the film was scored: I told it three moments: the title, the proof, the CTA.

It chose 120 BPM so each 4 s cut lands on a beat, planned sparse → peak → resolve, generated the track, measured it.

By its own beat estimate, every cut and moment is within 11 ms of a downbeat.

**6/**
Dogfooding found two real bugs. On a low-motion video, a reveal I'd told it about got no real lift. And a CTA I'd told it about never started the resolution.

Both fixed, with tests. That's what a plan-then-measure loop is for.

**7/**
Honest limits, v0.1: the generator is a synthesizer, not a composer. Use it as a reference bed, or sync your own licensed track. Codex and Docker are unverified.

Local-first, no telemetry. CLI + MCP + Claude Code Skill. Apache-2.0.

github.com/AAGAM17/MusicContext

---

## LinkedIn

AI agents can now generate remarkable video. They still pick the music by vibe.

A "cinematic" tag doesn't tell you that the product appears at 18.2 seconds, that the narrator is talking for half the timeline, or that your cuts land every 2 seconds. Those are the decisions that make an edit work, and they're all about time.

So I built MusicContext, an open-source music director for AI agents. It turns a video into a plan: what the story is, what the music needs to do, and where. Then it finds the music in your own library, or generates a reference bed offline, and mixes it in: trimmed, faded, ducked under speech, aligned to the beat.

The principle I'm proudest of: it never claims what it didn't measure. Every value is tagged as told by you, detected, inferred or estimated, and that survives into the output so an agent can report it honestly. A license is only "verified" if a sidecar file or a provider says so.

The launch film is scored by MusicContext itself. I told it three moments, and it chose a tempo that puts every cut on a beat, planned the arc, generated the track and measured it. By its own beat estimate, every cut and named moment lands within 11 ms of a downbeat.

Scoring my own film also found two real bugs in the arc logic, which are now fixed and tested. That's the loop working as intended.

It's v0.1 and I'd rather say the limits than hide them: there's no vision model, the generator is a synthesizer rather than a composer, and Codex support and the Docker image are still unverified. It's local-first with no telemetry, and works as a CLI, an MCP server and a Claude Code Skill. Apache-2.0.

github.com/AAGAM17/MusicContext

#AIagents #opensource #video #MCP
