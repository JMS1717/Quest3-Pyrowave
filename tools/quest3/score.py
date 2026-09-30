"""Compare the actual GPU RGBA readback against a reference, in RGB (alpha ignored)."""
import argparse
import json
import math
from pathlib import Path

def score(reference,actual,width,height):
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
        'method':'Same PyroWave frame, BT.709 full-range RGBA reference; no rescaling; alpha excluded.'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('reference');p.add_argument('actual')
    p.add_argument('--width',type=int,required=True);p.add_argument('--height',type=int,required=True)
    p.add_argument('--out',required=True);a=p.parse_args()
    result=score(Path(a.reference).read_bytes(),Path(a.actual).read_bytes(),a.width,a.height)
    Path(a.out).write_text(json.dumps(result,indent=2));print(json.dumps(result))

if __name__=='__main__':main()
