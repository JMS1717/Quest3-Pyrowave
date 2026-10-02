"""Compare the actual GPU RGBA readback against a reference, in RGB (alpha ignored)."""
import argparse
import json
import math
from pathlib import Path
import zlib

def score(reference,actual,width,height):
    if width<=0 or height<=0:raise ValueError('RGBA dimensions must be positive')
    size=width*height*4
    if len(reference)!=size or len(actual)!=size:raise ValueError('RGBA byte count does not match dimensions')
    errors=[0,0,0];maximum=[0,0,0]
    for i,(a,b) in enumerate(zip(reference,actual)):
        channel=i%4
        if channel==3:continue
        error=abs(a-b);errors[channel]+=error*error;maximum[channel]=max(maximum[channel],error)
    mse=sum(errors)/(width*height*3)
    return {'width':width,'height':height,'rgb_mse':mse,
        'rgb_psnr_db':10*math.log10(255*255/mse) if mse else None,
        'identical':mse==0,'max_channel_error':maximum,
        'method':'Same encoded frame and matching color/range reference; no rescaling; alpha excluded.'}

def readback_metadata(path,actual,width,height,allow_unverified=False):
    sidecar=Path(str(path)+'.json')
    if not sidecar.is_file():
        if allow_unverified:return {'status':'unverified','reason':'No readback sidecar; numerical score alone is not pixel acceptance.'}
        raise ValueError('Missing readback sidecar. Use the new GPU probe, or explicitly allow an unverified legacy readback.')
    metadata=json.loads(sidecar.read_text(encoding='utf-8'))
    if not isinstance(metadata,dict):raise ValueError('Readback sidecar must be an object')
    if metadata.get('schema_version')!=1 or metadata.get('readback_complete') is not True or metadata.get('producer_complete') is not True:
        raise ValueError('Readback or producer did not complete; refuse stale/unsupported pixel scores')
    if metadata.get('width')!=width or metadata.get('height')!=height or metadata.get('bytes')!=len(actual):
        raise ValueError('Readback dimensions/byte count do not match sidecar')
    checksum=f'{zlib.crc32(actual):08x}'
    if metadata.get('raw_rgba_crc32')!=checksum:
        raise ValueError('Readback bytes do not match sidecar checksum')
    usage=metadata.get('source_ahb_usage')
    if type(usage) is not int or usage<0:raise ValueError('Invalid source allocation usage')
    flip=metadata.get('flip_y')
    if type(flip) is not bool:raise ValueError('Invalid readback orientation')
    method=metadata.get('readback_method')
    if method=='gles_external_rgba8':
        expected_row='texture_v1_first' if flip else 'texture_v0_first'
        if not (usage&0x100):raise ValueError('GPU readback requires sampled-image allocation usage')
    elif method=='cpu_usage_compatible':
        expected_row='buffer_last_row_first' if flip else 'buffer_row0_first'
        if (usage&0xf) not in (2,3):raise ValueError('CPU readback requires CPU_READ allocation usage')
    else:raise ValueError('Unknown readback method')
    if metadata.get('row_order')!=expected_row:raise ValueError('Inconsistent readback row order')
    return {'status':'completed_consumer_readback','metadata':metadata,
            'limitations':'Checksum/provenance gate is not independent reference correctness, in-headset quality, optical FPS or MTP.'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('reference');p.add_argument('actual')
    p.add_argument('--width',type=int,required=True);p.add_argument('--height',type=int,required=True)
    p.add_argument('--out',required=True)
    p.add_argument('--allow-unverified-readback',action='store_true',help='Score legacy bytes lacking metadata, explicitly marked unverified')
    a=p.parse_args();actual=Path(a.actual).read_bytes()
    try:
        provenance=readback_metadata(a.actual,actual,a.width,a.height,a.allow_unverified_readback)
        result=score(Path(a.reference).read_bytes(),actual,a.width,a.height)
    except ValueError as error:p.error(str(error))
    result['readback']=provenance
    Path(a.out).write_text(json.dumps(result,indent=2));print(json.dumps(result))

if __name__=='__main__':main()
