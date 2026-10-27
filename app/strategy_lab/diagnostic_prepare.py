"""Prepare a fresh entry-disabled diagnostic lab from verified local NFI bytes."""
import argparse
import json
from pathlib import Path
from .prepare import prepare
from .supervisor import verify


def prepare_diagnostics(existing, output):
    existing,output=Path(existing).resolve(),Path(output).resolve()
    if output==existing or output.is_relative_to(existing):
        raise ValueError('SEPARATE_DIAGNOSTIC_DIRECTORY_REQUIRED')
    verify(existing)
    manifest=prepare(output,existing/'user_data/strategies')
    # The wrapper refuses every entry lease while this marker exists.
    (output/'user_data/DIAGNOSTICS_ENABLED').write_text('OBSERVATION_ONLY\n',encoding='utf-8')
    (output/'user_data/PAUSE_REQUESTED').write_text('OBSERVATION_ONLY\n',encoding='utf-8')
    summary={'scope':'PREPARED_NATIVE_PIPELINE_DIAGNOSTICS_V1','prepared_only':True,
             'existing_lab_modified':False,'atlas_baseline_modified':False,
             'network_requests':0,'grok_requests':0,'orders':0,
             'execution_authorized':False,'lab_id':manifest['lab_id'],
             'limitations':['Separate native process; cannot reconstruct the previous run',
                            'Same pinned upstream and strategy rules; entry leases always refused in this diagnostic lab']}
    (output/'diagnostic-preparation.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--existing',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();prepare_diagnostics(a.existing,a.output)
    print('DIAGNOSTICS_PREPARED; ENTRIES_DISABLED; EXISTING_LAB_UNCHANGED; NOTHING_STARTED')


if __name__=='__main__':main()
