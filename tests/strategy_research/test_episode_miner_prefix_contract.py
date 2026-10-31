"""Exercise the real episode producer, not a hand-authored timestamp fixture."""
from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
import json

from app.trade_intelligence.knowledge.mining import JsonlBarProvider,build_corpus
from app.strategy_research.episode_price_study import PriceBar,prefix_evidence,normalized_prefix_matches


def test_actual_miner_completion_times_match_normalized_open_times(tmp_path):
    start=datetime(2026,6,1,14,tzinfo=UTC)
    prices=[('1','1.1','.99','1.05'),('1.05','1.2','1.02','1.15'),
            ('1.15','1.3','1.1','1.25'),('1.25','1.3','1.2','1.22'),
            ('1.22','1.32','1.2','1.3'),('1.3','1.5','1.29','1.45')]
    bars=tuple(PriceBar('XYZ',start+timedelta(minutes=i),*[D(v) for v in values],D(1000))
               for i,values in enumerate(prices))
    source=tmp_path/'normalized.jsonl'
    source.write_text(''.join(json.dumps(dict(symbol=b.symbol,timestamp=b.timestamp.isoformat(),
        open=str(b.open),high=str(b.high),low=str(b.low),close=str(b.close),volume=str(b.volume),
        session='REGULAR'))+'\n' for b in bars))
    summary=build_corpus(JsonlBarProvider(source),tmp_path/'corpus',repository_commit='synthetic-contract-test')
    assert summary.accepted_unique>0
    for line in (tmp_path/'corpus/episodes.jsonl').read_text().splitlines():
        row=json.loads(line);at=datetime.fromisoformat(row['detected_timestamp'])
        # This equal timestamp caused V1 to reject every real sample by adding
        # an extra minute. It is already completed and must not be shifted forward.
        assert datetime.fromisoformat(row['bar_window'][-1]['timestamp'])==at
        proof=prefix_evidence(row,at)
        assert proof['ok'] and normalized_prefix_matches(proof,bars)
        changed=tuple(replace(b,volume=b.volume+1) for b in bars)
        assert not normalized_prefix_matches(proof,changed)
        row['bar_window'][-1]['timestamp']=(at+timedelta(minutes=1)).isoformat()
        assert not prefix_evidence(row,at)['ok']
