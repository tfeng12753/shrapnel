"""Compare the inherited declaration toggle with a supplied control checkout."""
import argparse
import json
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from score import evaluate_run
from tools.ab_eval import RUN_FLAGS, container_cpu, fingerprint, load_mutants, run_measured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--mutants', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    tests = json.loads((ROOT / 'sample/tests.json').read_text())['tests']
    heldout, patches, skipped, _ = load_mutants(args.mutants, 'holdout', False, tests)
    sample = json.loads((ROOT / 'sample/answers.json').read_text())['commits']
    datasets = {
        'sample': (sample, {key: ROOT / 'sample/commits' / f'{key}.patch' for key in sample}),
        'heldout': (heldout, patches),
    }
    report = {'control_fingerprint': fingerprint(args.control),
              'candidate_fingerprint': fingerprint(ROOT), 'image': args.image,
              'miss_penalty_seconds': 300, 'skipped': skipped, 'datasets': {}}
    for dataset, (answers, patch_paths) in datasets.items():
        rows = report['datasets'][dataset] = {}
        with tempfile.TemporaryDirectory(prefix='agent6-data-') as temporary:
            data = Path(temporary)
            data.chmod(0o755)
            (data / 'commits').mkdir()
            shutil.copyfile(ROOT / 'sample/tests.json', data / 'tests.json')
            for key in answers:
                shutil.copyfile(patch_paths[key], data / 'commits' / f'{key}.patch')
            for label, revision, extra in (
                ('main', args.control, []), ('off', ROOT, ['--no-inherited-typeddict']),
                ('on', ROOT, ['--inherited-typeddict']),
            ):
                runs = []
                for repeat in range(args.repeats):
                    name = 'selector-agent6-' + uuid.uuid4().hex[:12]
                    command = ['docker', 'run', '--name', name, *RUN_FLAGS,
                               '--volume', f'{data}:/data:ro']
                    for path in sorted(revision.glob('*.py')):
                        command.extend(['--volume', f'{path}:/app/{path.name}:ro'])
                    for local, remote in ((revision / 'selector-config.json', 'selector-config.json'),
                                          (revision / 'maps/dependencies.json.gz', 'dependencies.json.gz')):
                        command.extend(['--volume', f'{local}:/app/{remote}:ro'])
                    command.extend([args.image, 'python', '/app/tools/timed_run.py', *extra])
                    run = run_measured(command, 240)
                    # Timed-out runs must not leave a container running.
                    if run['returncode'] == 'timeout':
                        import subprocess
                        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
                    evaluation = evaluate_run(run['stdout'].encode(), tests, answers)
                    cpu = container_cpu(run['stderr_tail'])
                    if run['returncode'] != 0 or not evaluation['ok'] or cpu is None:
                        raise RuntimeError((label, dataset, run, evaluation))
                    selected = {row['commit']: {n for worker in row['workers'] for n in worker}
                                for row in map(json.loads, run['stdout'].splitlines())}
                    missed = {key: sorted(set(answer['failing']) - selected[key])
                              for key, answer in answers.items() if set(answer['failing']) - selected[key]}
                    simulated = evaluation['simulated_milliseconds'] / 1000
                    runs.append({'cpu_seconds': cpu, 'simulated_seconds': simulated,
                                 'total_seconds': simulated + cpu, 'missed_tests': missed})
                    print(dataset, label, repeat + 1, runs[-1], flush=True)
                rows[label] = {'patches': len(answers), 'runs': runs,
                               'median_total_seconds': statistics.median(r['total_seconds'] for r in runs),
                               'median_cpu_seconds': statistics.median(r['cpu_seconds'] for r in runs)}
    args.out.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
