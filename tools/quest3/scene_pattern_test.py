"""CPU raster checks for the resolution-comparison chart; no VR/GPU access."""
import unittest
import numpy as np
from .stereo_scene import eye_pattern

PROJECTIONS = ((-1.37638, .83910, -1.42815, .96569),
               (-.83910, 1.37638, -1.42815, .96569))
EYES = (('LEFT', (32,32,200)), ('RIGHT', (200,64,32)))


class ScenePatternTests(unittest.TestCase):
    def test_reference_scale_preserves_existing_pixels(self):
        for (label,color),projection in zip(EYES,PROJECTIONS):
            for quality in (False,True):
                with self.subTest(eye=label,quality=quality):
                    old=eye_pattern(2080,2208,label,color,projection,quality)
                    normalized=eye_pattern(2080,2208,label,color,projection,quality,True)
                    self.assertTrue(np.array_equal(old,normalized))

    def test_both_projected_panels_keep_position_and_size(self):
        for (label,color),projection in zip(EYES,PROJECTIONS):
            bounds=[]
            for width,height in ((2080,2208),(2544,2704),(3072,3216)):
                image=eye_pattern(width,height,label,color,projection,True,True)
                y,x=np.where(np.all(image==color,axis=2))
                self.assertGreater(len(x),0)
                bounds.append((x.min()/width,y.min()/height,x.max()/width,y.max()/height))
            with self.subTest(eye=label):
                self.assertLess(np.max(np.ptp(np.array(bounds),axis=0)),.001)
        # The two optical centres intentionally differ; never mirror one
        # eye's FOV onto the other when creating the source chart.
        self.assertNotEqual(PROJECTIONS[0],PROJECTIONS[1])

    def test_one_reference_pixel_stripes_gain_source_samples(self):
        projection=PROJECTIONS[0]
        cx=int(2080*-projection[0]/(projection[1]-projection[0]))
        cy=int(2208*-projection[2]/(projection[3]-projection[2]))
        runs=[]
        for width,height in ((2080,2208),(3072,3216)):
            image=eye_pattern(width,height,'LEFT',EYES[0][1],projection,True,True)
            sx,sy=width/2080,height/2208
            row=image[round((cy-760+490+10)*sy),round((cx-650+40)*sx):round((cx-650+1240)*sx)]
            red=np.all(row==(0,0,255),axis=1)
            cyan=np.all(row==(255,255,0),axis=1)
            self.assertTrue(np.all(red|cyan))
            edges=np.flatnonzero(red[1:]!=red[:-1])+1
            runs.append(np.diff(np.r_[0,edges,len(row)]))
        self.assertTrue(np.all(runs[0]==1))
        self.assertGreater(runs[1].max(),1)


if __name__=='__main__':unittest.main()
