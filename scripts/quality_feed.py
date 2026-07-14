#!/usr/bin/env python3
"""Quality-feedback sidecar for Ringer. Posts per-task quality samples to Feeder."""

import argparse
import json
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def parse_grade(grade_str):
    """Parse a KEY=SCORE string. Returns (key, score) or raises ValueError."""
    if '=' not in grade_str:
        raise ValueError(f"Invalid grade format: {grade_str}")
    key, score_str = grade_str.split('=', 1)
    try:
        score = float(score_str)
    except ValueError:
        raise ValueError(f"Invalid score: {score_str}")
    if not (0.0 <= score <= 1.0):
        raise ValueError(f"Score out of range [0..1]: {score}")
    return key, score


def main():
    parser = argparse.ArgumentParser(description='Quality feedback sidecar for Ringer')
    parser.add_argument('--state', required=True, help='Path to state JSON file')
    parser.add_argument('--grade', action='append', default=[], dest='grades',
                        help='Explicit grade for a task key: KEY=SCORE')
    parser.add_argument('--default-pass', type=float, default=0.8,
                        help='Default quality score for pass status')
    parser.add_argument('--default-fail', type=float, default=0.0,
                        help='Default quality score for fail status')
    parser.add_argument('--feeder-base', default='http://localhost:3001',
                        help='Base URL for Feeder API')
    parser.add_argument('--model-id-form', choices=['combined', 'bare'], default='combined',
                        help='How to format model_id')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print samples and skipped without posting')
    parser.add_argument('--post', action='store_true',
                        help='POST samples to Feeder')
    args = parser.parse_args()

    # Parse explicit grades
    explicit_grades = {}
    for grade_str in args.grades:
        try:
            key, score = parse_grade(grade_str)
            explicit_grades[key] = score
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(grade_str, file=sys.stderr)
            sys.exit(1)

    # Load state
    try:
        with open(args.state, 'r', encoding='utf-8') as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(f"Error: cannot load state JSON: {e}", file=sys.stderr)
        sys.exit(1)

    tasks = state.get('tasks', [])
    samples = []
    skipped = []

    for task in tasks:
        task_key = task.get('key', '')
        task_status = task.get('status', '')
        task_model = task.get('model', '')
        feeder = task.get('feeder')

        # Determine task_class from model field
        task_class = None
        if task_model and task_model.startswith('feeder/auto/'):
            suffix = task_model[len('feeder/auto/'):]
            if suffix:  # non-empty after the prefix
                task_class = suffix
        # If model is exactly "feeder/auto" or missing, omit task_class

        # Check skip conditions
        if feeder is None or not isinstance(feeder, dict):
            skipped.append({"key": task_key, "reason": "no feeder dict - no served model"})
            continue

        served = feeder.get('served')
        if not served or not isinstance(served, list) or len(served) == 0:
            skipped.append({"key": task_key, "reason": "no served model in feeder dict"})
            continue

        if feeder.get('mixed_models', False):
            skipped.append({"key": task_key, "reason": "mixed models - multiple models served"})
            continue

        # Build model_id
        first_served = served[0]
        platform = first_served.get('platform', '')
        model_id = first_served.get('model_id', '')

        if args.model_id_form == 'combined':
            model_id_full = f"{platform}/{model_id}" if platform and model_id else model_id
        else:  # bare
            model_id_full = model_id

        # Determine quality_score
        if task_key in explicit_grades:
            quality_score = explicit_grades[task_key]
        elif task_status == 'pass':
            quality_score = args.default_pass
        else:
            quality_score = args.default_fail

        # Build sample
        sample = {
            "model_id": model_id_full,
            "quality_score": quality_score,
            "judge": "ringer"
        }
        if task_class is not None:
            sample["task_class"] = task_class

        samples.append(sample)

    result = {"samples": samples, "skipped": skipped}

    if args.post:
        base_url = args.feeder_base.rstrip('/')
        endpoint = f"{base_url}/api/model-perf/sample"
        any_failed = False

        for sample in samples:
            try:
                data = json.dumps(sample).encode('utf-8')
                req = Request(endpoint, data=data,
                              headers={'Content-Type': 'application/json'},
                              method='POST')
                with urlopen(req, timeout=30) as resp:
                    status = resp.getcode()
                    # /sample can answer 200 with {ok:false, reason:...} for a
                    # model with no canonical link — that is NOT a recorded
                    # sample, so require ok truthy, not just HTTP 2xx.
                    try:
                        body = json.loads(resp.read().decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        body = {}
                    ok = bool(body.get("ok", 200 <= status < 300))
                    sample["posted"] = (200 <= status < 300) and ok
                    if sample["posted"]:
                        print(f"Posted sample: status={status} ok={ok}", file=sys.stderr)
                    else:
                        print(
                            f"Sample rejected: status={status} "
                            f"reason={body.get('reason', 'unknown')!r}",
                            file=sys.stderr)
                        any_failed = True
            except (HTTPError, URLError, OSError) as e:
                print(f"Failed to post sample: {e}", file=sys.stderr)
                sample["posted"] = False
                any_failed = True

        print(json.dumps(result))
        if any_failed:
            sys.exit(3)
        sys.exit(0)
    else:
        # dry-run or no flag: behave like dry-run
        print(json.dumps(result))
        sys.exit(0)


if __name__ == '__main__':
    sys.exit(main())
