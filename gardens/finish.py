"""One unattended run: update/resume, official names/exports, then safe cleanup."""
import ctypes
import sys
from contextlib import contextmanager
from pathlib import Path

from .checkpoint import require_checkpoint
from .download import atomic_json

REPORT='finish_all_report.json'
ES_CONTINUOUS=0x80000000
ES_SYSTEM_REQUIRED=0x00000001


@contextmanager
def keep_awake(enabled=True):
    """Temporarily prevent automatic Windows sleep; allow the display to sleep."""
    state={'requested':bool(enabled),'platform':sys.platform,'active':False,'restored':False,'warning':None}
    api=None;previous=None
    if enabled and sys.platform=='win32':
        try:
            api=ctypes.windll.kernel32.SetThreadExecutionState
            api.argtypes=[ctypes.c_uint];api.restype=ctypes.c_uint
            previous=api(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)
            if not previous:raise OSError('Windows did not accept the keep-awake request')
            state['active']=True
        except (AttributeError,OSError) as exc:
            state['warning']=str(exc)
            print('Keep-awake warning: '+str(exc)+'. Check Windows sleep settings before leaving.',flush=True)
    try:yield state
    finally:
        if state['active']:
            try:
                if not api(previous|ES_CONTINUOUS):raise OSError('Windows did not accept the sleep-state restore request')
                state['restored']=True
            except (AttributeError,OSError) as exc:state['warning']=str(exc)
            state['active']=False


def finish_all(output,update_callable,cleanup_callable=None,plan_only=False):
    """Continue cleanup after expected incomplete results, never after an abort.

    update_callable is the normal --update --include-pending workflow, including
    its official name lookup and grouped PNG export. It runs synchronously, so
    all of its workers have stopped before the cleanup stage can begin.
    """
    out=Path(output).resolve();require_checkpoint(out,writable=False)
    target=out/REPORT
    for path in (target,target.with_name(REPORT+'.tmp')):
        if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):
            raise ValueError('A finish-all report must not redirect through a symlink or junction')
    steps=[{'name':'update','status':'pending','exit_code':None},
           {'name':'cleanup','status':'pending','exit_code':None}]
    report={'schema':1,'status':'in_progress','output':str(out),'plan_only':bool(plan_only),
            'steps':steps,'exit_code':None,'report_path':str(target),
            'outputs':{'scene_tests':str(out/'all_scene_test_summary.json'),
                       'name_matches':str(out/'scene_name_matches.csv'),
                       'naming':str(out/'naming_report.json'),
                       'solution_refresh':str(out/'solution_refresh_report.json'),
                       'pose_refresh':str(out/'pose_refresh_report.json'),
                       'named_images':str(out/'named_scenes'),
                       'cleanup':str(out/'cleanup_incomplete_report.json')}}
    with keep_awake(not plan_only) as awake:
        report['keep_awake']=awake
        atomic_json(target,report)
        print('Finish-all: updating catalogues, resuming unfinished scenes, matching official names and organizing exports.',flush=True)
        steps[0]['status']='running';atomic_json(target,report)
        try:
            code=update_callable()
            if not isinstance(code,int):raise ValueError('Update returned an invalid exit code')
        except KeyboardInterrupt:
            code=130
        except SystemExit as exc:
            code=exc.code if isinstance(exc.code,int) else 2
        except Exception as exc:
            code=2;steps[0]['error']=f'{type(exc).__name__}: {exc}'
        steps[0].update(status='completed' if code==0 else ('partial' if code==1 else ('interrupted' if code==130 else 'failed')),exit_code=code)
        if code not in (0,1):
            steps[1].update(status='skipped',reason='Update was interrupted or failed before completing its workflow')
            report.update(status='interrupted' if code==130 else 'failed',exit_code=code)
        elif plan_only:
            steps[1].update(status='skipped',reason='Plan only; no previews are deleted')
            report.update(status='planned',exit_code=code)
        else:
            steps[1]['status']='running';atomic_json(target,report)
            print('Finish-all: deleting obsolete incomplete previews only from verified passed scene versions.',flush=True)
            try:
                if cleanup_callable is None:
                    from .cleanup import cleanup_incomplete
                    cleanup_callable=cleanup_incomplete
                cleaned=cleanup_callable(out,delete=True)
                cleanup_code=130 if cleaned['status']=='interrupted' else (0 if cleaned['status']=='completed' else 1)
                steps[1].update(status=cleaned['status'],exit_code=cleanup_code,summary=cleaned.get('summary',{}))
            except KeyboardInterrupt:
                cleanup_code=130;steps[1].update(status='interrupted',exit_code=130)
            except Exception as exc:
                cleanup_code=2;steps[1].update(status='failed',exit_code=2,error=f'{type(exc).__name__}: {exc}')
            final=cleanup_code if cleanup_code in (2,130) else (1 if code or cleanup_code else 0)
            report.update(status='completed' if final==0 else ('partial' if final==1 else ('interrupted' if final==130 else 'failed')),exit_code=final)
    # Save after the context exits so the report records sleep-state restoration.
    atomic_json(target,report)
    print(f"Finish-all: {report['status']}. Report: {target}",flush=True)
    for step in steps:
        if step.get('error'):print(step['name']+': '+step['error'],flush=True)
    return report['exit_code']
