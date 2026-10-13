# Quality gates

The project has a deterministic media benchmark that generates a six-second
640x360/30fps video with a known audio tone, then validates the media and runs
Audio QC. It is a regression gate, not a substitute for an Arabic/Windows/
NVIDIA acceptance set.

Run it locally:

```bash
python -m scripts.quality_benchmark --output quality-benchmark.json
```

The command fails unless all gates pass: video validation, 640x360 dimensions,
30 fps, approximately six seconds, an audio stream, and Audio QC status
`pass`. The report includes `gate_failures`; temporary fixture paths are
redacted so the JSON is suitable for CI comparison. `elapsed_seconds` is only
a performance observation and is not used as a pass/fail gate.

CI stores the report beside the wheel, SBOM, and dependency-license report.

## OpenTimelineIO

OTIO is optional so the core install stays lightweight:

```bash
pip install -e '.[otio]'
python -m scripts.otio_export --segments /path/to/segments.json \
  --media /path/to/source.mp4 --output /path/to/timeline.otio
```

The input JSON may be either a list of segments or an object with a
`segments` list. Each segment uses `start_time`/`end_time` or `start`/`end`,
and may include `title`. The timeline contains one video track with source
ranges and ViralCutter segment metadata. Premiere XML remains available for
the existing workflow.
