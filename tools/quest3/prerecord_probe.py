"""Parse bounded producer windows; mixed-arm or invalid rows are unusable."""
import math

INTEGER_FIELDS = ('completed','prepared','submitted','canceled','deferred','rows','arm_prepare','arm_control','max_gpu')
TIME_FIELDS = ('queue_mean_ms','queue_max_ms','record_mean_ms','completion_mean_ms','gpu_mean_ms')

def parse_row(line):
    marker='[Q3PW_PRERECORD] '
    if marker not in line: return None
    fields=dict(pair.split('=',1) for pair in line.split(marker,1)[1].split() if '=' in pair)
    try:
        row={k:int(fields[k]) for k in INTEGER_FIELDS}
        row.update({k:float(fields[k]) for k in TIME_FIELDS})
    except (KeyError,ValueError): return None
    if any(row[k]<0 for k in INTEGER_FIELDS) or any(not math.isfinite(row[k]) or row[k]<0 for k in TIME_FIELDS): return None
    if row['rows']!=120 or row['arm_prepare']+row['arm_control']!=120 or row['max_gpu']!=1: return None
    if row['submitted']+row['canceled']>row['prepared'] or row['queue_mean_ms']>row['queue_max_ms']+0.000002: return None
    row['arm']='prepare' if row['arm_prepare']==120 else 'control' if row['arm_control']==120 else 'mixed'
    row['comparison_usable']=row['arm']!='mixed'
    return row

def deltas(previous,current):
    """Negative counters mean process/session change, never wrap them into gain."""
    keys=('completed','prepared','submitted','canceled','deferred')
    if any(current[k]<previous[k] for k in keys): return None
    return {k:current[k]-previous[k] for k in keys}
