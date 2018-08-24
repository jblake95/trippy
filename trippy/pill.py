#! /usr/bin/env python

"""
Copyright (C) 2016  Wesley Fraser

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

__author__ = 'Wesley Fraser (@wtfastro, github: fraserw <westhefras@gmail.com>), Academic email: wes.fraser@qub.ac.uk'


import numpy as num
import sys
import pylab as pyl
from matplotlib import gridspec
from scipy import interpolate as interp
from . import bgFinder

from astropy.visualization import interval
from stsci import numdisplay

from .trippy_utils import expand2d,line





class pillPhot:
    """
    The pill aperture photometry. Intialized with the image data, and repFact subsampling factor.

    To use is pretty simple. Call the object with inpur parameters:
    -x/y in IRAF coordinates (not numpy)
    -r,l,a the radius, length, and angle in pixels of the pill aperture
        --radius is the radiu of the half circle end cap, and the width of the linear section, l is the linear length
        --angle is +-90 degrees of horizontal
    -sky radius is the radius of the pill shape (same angle and length as above) inside of which is excluded for both
     photometry and background measurement
    -width is the width of the square inside of which the background is estimated
    -zpt and exptime are the zeropoint and exposure time of the image in question
    -enableBGselection=True allows the user to zoom on a good background region to improve the bg esimation
    -display=True to see the aperture
    -mode is the method of background esimaation. Best options are 'smart', 'median', or 'gauss' (see bgFinder.py)
    -trimBGHighPix=3.5 is a sigma clip of the background, ignoring all values above the sigma threshold provided.

    """
    def __init__(self,data,zscale=True,repFact=10):
        self.data=data*1.0
        self.zscale=zscale
        self.repFact=repFact
        self.l0=None
        self.bg=None

        self.aperFunc=None



    def roundAperCorr(self,r):
        """
        Return round aperture correction at radius r interpolated from values computed in computeRoundAperCorrFromSource.
        """
        if self.aperFunc!=None:
            return self.aperFunc(r)-num.min(self.aperMags)
        else:
            raise Exception('Need to call computeRoundAperCorrFromSource first')

    def computeRoundAperCorrFromSource(self,x,y,radii,skyRadius,width=20.,mode='smart',displayAperture=False,display=False):
        """
        Compute apeture corrections at the specified star coordinates and the specified radii numpy array.

        skyRadius is the radius outside of which the background is estimated.
        mode is the method by which the background is estimated.
        width is the width of the cutout. Make this bigger than skyRadius!
        displayAperture=True to see the aperture at each radius.
        display=True to see a radial aperture correction plot.
        """

        #if radii[0]*width<=skyRadius:
        #    raise Exception('Must use a width larger than skyRadius divided by the first radius given.\n')
        if skyRadius>width:
            raise Exception('Must use a width that is larger than the skyRadius')

        self.radii=radii*1.

        """
        #individual apertures
        self.aperMags=[]
        for ii in range(len(radii)):
            self(x,y,radii[ii],l=0.,a=0.,width=width,skyRadius=skyRadius,backupMode=mode,display=displayAperture)
            self.aperMags.append(self.magnitude)
        """
        #more efficient version where apertures are all passed as an array
        self(x, y, radii, l=0., a=0., width=width, skyRadius=skyRadius, backupMode=mode, display=displayAperture)
        self.aperMags = self.magnitude


        self.aperFunc=interp.interp1d(radii,self.aperMags)

        if display:
            aperFig=pyl.figure('Aperture Corrections')
            pyl.plot(radii,self.aperMags)
            pyl.show()
            pyl.close()


    def SNR(self,gain=1.64,readNoise=3.82,useBGstd=False,nImStacked=1,verbose=False):
        """
        Compute the SNR and uncertainty of the flux measurement.

        Switch useBGstd to true to use the measured standard deviation of
        background pixel values as a measure of the background+readnoise
        uncertainty instead of the background flux. Better for IR data. This
        will autumatically set readNoise=0.
        """
        star=gain*self.sourceFlux
        if not useBGstd:
            bg=gain*self.nPix*self.bg/nImStacked
            rn=self.nPix*readNoise*readNoise/nImStacked
        else:
            bg=gain*self.nPix*self.bgstd**2
            rn=0.
        self.snr=star*(star+bg+rn)**-0.5
        self.dmagnitude=(2.5/num.log(10.))*(1./self.snr)

        if verbose:
            print("   SNR: %s"%(self.snr))
            print("   Flux: %s"%(self.sourceFlux))
            print("   Background: %s"%(self.bg))
            print("   Background STD: %s"%(self.bgstd))
            print("   Num Pixels : %s"%(self.nPix))
            print()

    def __call__(self,xi,yi,radius=4.,l=5.,a=0.01,width=20.,skyRadius=8.,zpt=27.0,exptime=1.,
                 enableBGSelection=False, display=False,
                 verbose=False, backupMode='fraserMode', forceBackupMode = False,
                 trimBGHighPix=False, zscale=True, connectKeyGatherer = False):
        """
        Perform the actual photometry.

        angle in degrees clockwise +-90 degrees from +x
        Length in pixels.
        Coordinates are in iraf coordinates, not numpy.

        -width is the outer dimension of the image region considered.
        That is, 2*width*2*width is the image subsection size.

        set trimBGHighPix to some value ,x, to trim the background
        -this is done by first estimating the background. Then, it trims
        all pixels with value v>bg+x*bgstd. That is, it provides a rough
        sigma cut to get rid of glaringly bright sources that might affect
        the bg estimate.
        -the bg is then restimated.

        -connectKeyGatherer is a way to gather key presses while displaying the
        fits image. This is useful if you wish to do a manual marking (eg. good
        or bad) while doing photometry. If set to True, this will return an
        array of all the keys pressed.
        """
        #Single-letter variables = super painful debugging
        #I'll leave them in the function call for backwards compatibility,
        #but will remove below.
        angle = a
        length = l
        del(a, l)

        #Set up some of the True/False statements that gets used repeatedly:
        singleAperture = isinstance(radius, (float, num.float64))
        multipleApertures = isinstance(radius, num.ndarray)
        if not singleAperture | multipleApertures:
          raise Exception('Aperture size not understood. ' +
                          'It seems to be neither an array or a single value')
        #Check whether aperture(s) is integer: if True convert to float.
        integerAperture = isinstance(radius, (int, num.integer))
        if multipleApertures:
            integerAperture = issubclass(radius.dtype.type, num.integer)
        if integerAperture:
            radius *= 1.
            integerAperture = False

        if display:
            #setup the necessary subplots

            self.dispFig = pyl.figure('Image Display')
            if enableBGSelection:
                #dispFig.subplots_adjust(wspace = 0.0,hspace = 0.0)
                self.dispGS = gridspec.GridSpec(1, 2)#, height_ratios = [3.5,1], width_ratios = [3.5,1])
                self.dispAx = pyl.subplot(self.dispGS[0])
            else:

                self.dispAx = self.dispFig.add_subplot(111)

            #if connectKeyGatherer:
            #    self.dispFig.callbacks.connect(keyGatherer, 'key_press_event')


        x = xi-0.5
        y = yi-0.5

        #if l+radius<width or l+skyRadius<width:
        #    raise Exception("Width must be large enough to include both the full aperture, and the sky radius.")

#        if angle > 90 or angle < -90 or length < 0 or num.min(radius) < 0:
        if angle > 90 or angle < -90:
            angle = angle % 180.
            if verbose:
                print("Warning! You gave a bad angle. I'll fix it for you.")
        if length < 0:
            length = -length
            if verbose:
                print("Warning! You gave a bad length. I'll fix it for you.")
        if num.min(radius) < 0:
            raise Exception('Aperture radius must be positive!')

        if singleAperture:
            image = self.__lp__(x=x + 1., y=y + 1.,
                                radius=radius, l=length, a=angle, w=int(width))
            mask = self.mask
        elif multipleApertures:
            image = []
            mask = []
            for jj in range(len(radius)):
                image.append(self.__lp__(x=x + 1., y=y + 1.,
                                         radius=radius[jj], l=length, a=angle,
                                         w=int(width)))
                mask.append(self.mask)

        if display and self.l0!=None:
            l0 = self.l0
            l1 = self.l1
            l2 = self.l2
            l3 = self.l3

        bgstd = -1.

        if skyRadius == None:
            if singleAperture:
                skyImage = image*0.0
            elif multipleApertures:
                skyImage = image[0]*0.0
            bg=0.0
        else:
            skyImage = self.__lp__(x=x + 1., y=y + 1., radius=skyRadius,
                                   l=length, a=angle, w=int(width),
                                   retObj=False)
            bgmask = self.bgmask

            rebinnedSkyImage = num.zeros(num.array(skyImage.shape)/self.repFact)
            (aa,bb) = skyImage.shape
            for ii in range(0,aa,self.repFact):
                for jj in range(0,bb,self.repFact):
                    n = num.sum(bgmask[ii:ii+self.repFact,jj:jj+self.repFact])
                    if n==self.repFact*self.repFact:
                        rebinnedSkyImage[ii/self.repFact,jj/self.repFact] = num.sum(skyImage[ii:ii+self.repFact, jj:jj+self.repFact])

            w = num.where(rebinnedSkyImage!=0.0)
            bgf = bgFinder.bgFinder(rebinnedSkyImage[w])
            if display and enableBGSelection:
                bgf.plotAxis = self.dispFig.add_subplot(self.dispGS[1])

            if not trimBGHighPix:
                bg = bgf.smartBackground(display=display, backupMode=backupMode, forceBackupMode = forceBackupMode)
                bgstd = num.std(rebinnedSkyImage[w])
            else:
                #trimming BG high pix
                bg = bgf.smartBackground(backupMode=backupMode, forceBackupMode = forceBackupMode)
                bgstd = num.std(rebinnedSkyImage[w])

                W = num.where(rebinnedSkyImage[w]<bg+trimBGHighPix*bgstd)
                bgf = bgFinder.bgFinder(rebinnedSkyImage[w][W])
                if display and enableBGSelection:
                    bgf.plotAxis = self.dispFig.add_subplot(self.dispGS[1])
                bg = bgf.smartBackground(display=display, backupMode=backupMode, forceBackupMode = forceBackupMode)
                bgstd = num.std(rebinnedSkyImage[w][W])

        if singleAperture:
            W = num.where(mask != 0.0)
            flux = num.sum(image) - len(W[0]) * bg / (self.repFact ** 2)
        elif multipleApertures:
            flux = []
            for jj in range(len(radius)):
                W = num.where(mask[jj] != 0.0)
                flux.append(num.sum(image[jj]) -
                            len(W[0]) * bg / (self.repFact ** 2))
            flux = num.array(flux)

        self.nPix = num.sum(mask) / (self.repFact ** 2)
        self.sourceFlux = flux
        self.bg = bg
        self.bgstd = bgstd
        self.exptime = exptime
        self.magnitude = zpt-2.5*num.log10(self.sourceFlux/self.exptime)

        if display:
            if trimBGHighPix:
                w = num.where(skyImage>(bg+trimBGHighPix*bgstd)/(self.repFact*self.repFact))
                skyImage[w] = 0

            if multipleApertures:
                im = skyImage+image[-1]
            elif singleAperture:
                im = skyImage+image

            if self.zscale:
                (z1,z2) = numdisplay.zscale.zscale(im)
                norm = interval.ManualInterval(z1,z2)
                self.dispAx.imshow(norm(im),interpolation='nearest',origin='lower')
            else:
                w = num.where(im==0.0)
                im[w]+=self.bg*0.7/(self.repFact*self.repFact)
                im = num.clip(im,num.min(im),num.max(image))
                self.dispAx.imshow(im,interpolation='nearest',origin='lower')
            if self.l0 is not None:
                self.dispAx.plot(num.linspace(l0.xlim[0], l0.xlim[1], 100),
                                 l0(num.linspace(l0.xlim[0], l0.xlim[1], 100)),
                                 'w-', lw=2.)
                self.dispAx.plot(num.linspace(l2.xlim[0], l2.xlim[1], 100),
                                 l2(num.linspace(l2.xlim[0], l2.xlim[1], 100)),
                                 'w-', lw=2.)
                mx0 = (l0.xlim[0] + l2.xlim[0]) / 2
                my0 = (l0.ylim[0] + l2.ylim[0]) / 2
                a0 = num.arctan2(l0.ylim[0] - my0, l0.xlim[0] - mx0)
                a1 = num.arctan2(l2.ylim[0] - my0, l2.xlim[0] - mx0)
                a01 = num.linspace(a0, a1, 25)
                outerRadius = radius if singleAperture else radius[-1]
                semiCircX = num.cos(a01) * outerRadius * self.repFact
                semiCircY = num.sin(a01) * outerRadius * self.repFact
                mx1 = (l0.xlim[1] + l2.xlim[1]) / 2
                my1 = (l0.ylim[1] + l2.ylim[1]) / 2
                my0, my1 = ((my1, my0) if ((angle < 0) & (angle > -90))
                            else (my0, my1))
                self.dispAx.plot(mx0 - semiCircX, my0 - semiCircY, 'w-', lw=2)
                self.dispAx.plot(mx1 + semiCircX, my1 + semiCircY, 'w-', lw=2)

            if enableBGSelection:
                print('Current background value: %.3f'%(self.bg))
                self.dispAx.set_title('To improve background measurement, zoom on\na good background region, then close.')

                (ox0,ox1) = self.dispAx.get_xlim()
                (oy0,oy1) = self.dispAx.get_ylim()


                (A,B) = im.shape

                self.dispAx.callbacks.connect('xlim_changed',self._on_xlims_change)
                self.dispAx.callbacks.connect('ylim_changed',self._on_ylims_change)
                #dispAx.callbacks.connect('lim_changed',self._on_lims_change)
                #all of these are needed in _on_lims_change
                self._bgFind = bgf
                self._rbsi = rebinnedSkyImage
                self._AB = im.shape
                self._bgm = backupMode
                self._fbgm = forceBackupMode

                pyl.show()

                (x0,x1) = self.dispAx.get_xlim()
                (y0,y1) = self.dispAx.get_ylim()
                self.bgSamplingRegion = [x0/self.repFact, x1/self.repFact, y0/self.repFact, y1/self.repFact]

                #need to clear the histogram first
                #need to update the positions so that the code below this ifstatement actualy fires
                if ox0==x0 and ox1==x1 and oy0==y0 and oy1==y1: return

                x0 = max(0,x0)/self.repFact
                y0 = max(0,y0)/self.repFact
                x1 = min(B,x1)/self.repFact
                y1 = min(A,y1)/self.repFact

                rebinnedSkyImage = rebinnedSkyImage[int(y0):int(y1), int(x0):int(x1)]
                w = num.where(rebinnedSkyImage!=0.0)
                bgf = bgFinder.bgFinder(rebinnedSkyImage[w])
                bg = bgf.smartBackground(display=False, backupMode=backupMode, forceBackupMode = forceBackupMode)
                bgstd = num.std(rebinnedSkyImage[w])


                if singleAperture:
                    W = num.where(mask != 0.0)
                    flux = num.sum(image) - len(W[0]) * bg / (self.repFact ** 2)
                elif multipleApertures:
                    flux = []
                    for jj in range(len(radius)):
                        W = num.where(mask[jj] != 0.0)
                        flux.append(num.sum(image[jj]) -
                                    len(W[0]) * bg / (self.repFact ** 2))
                    flux = num.array(flux)

                self.nPix = num.sum(mask) / (self.repFact ** 2)
                self.sourceFlux = flux
                self.bg = bg
                self.bgstd = bgstd
                self.exptime = exptime
                self.magnitude = zpt-2.5*num.log10(self.sourceFlux/self.exptime)
                self.bgSamplingRegion = [x0,x1,y0,y1]

            else: pyl.show()
        if verbose: print(num.sum(image),self.sourceFlux,self.bg,zpt-2.5*num.log10(flux))


    #helper functions to enable zoom and histogram updating
    def _on_xlims_change(self, axis):
        (x0, x1) = self.dispAx.get_xlim()
        self._x01 = (x0, x1)
    def _on_ylims_change(self, axis):

        _origDat = num.copy(self._bgFind.data)
        (A, B) = self._AB
        (x0, x1) = self._x01
        (y0, y1) = self.dispAx.get_ylim()

        x0 = max(0, x0) / self.repFact
        y0 = max(0, y0) / self.repFact
        x1 = min(B, x1) / self.repFact
        y1 = min(A, y1) / self.repFact

        #print x0,x1,y0,y1
        junk = self._rbsi[int(y0):int(y1),int(x0):int(x1)]
        w = num.where(junk!=0)
        self._bgFind.plotAxis.cla()
        self._bgFind.data = junk[w]
        self._event_background = self._bgFind.smartBackground(display=False, backupMode=self._bgm, forceBackupMode=self._fbgm)
        self._bgFind.background_display(self._event_background)
        self._bgFind.data = num.copy(_origDat)
        pyl.draw()


    def __lp__(self,x,y,radius,l,a,w,retObj=True):
        #Single-letter variables = super painful debugging
        #I'll leave them in the function call for backwards compatibility,
        #but will remove below.
        angle = a
        length = l
        del(a, l)
        ang=num.radians(angle)

        (A,B)=self.data.shape

        a=max(0,int(y-1)-w)
        b=min(A,int(y-1)+w+1)
        c=max(0,int(x-1)-w)
        d=min(B,int(x-1)+w+1)
        data=self.data[a:b,c:d]


        #repData=num.repeat(num.repeat(data,self.repFact,axis=0),self.repFact,axis=1)/(self.repFact*self.repFact)
        repData=expand2d(data,self.repFact)
        (A,B)=repData.shape

        if ((x < w) and (y < w)):
            cx = num.array([(x - 1) * self.repFact, (y - 1) * self.repFact])
        elif (x < w):
            cx = num.array([(x - 1) * self.repFact, (y - int(y) + w) * self.repFact])
        elif (y < w):
            cx = num.array([(x - int(x) + w) * self.repFact, (y - 1) * self.repFact])
        else:
            cx = num.array([(x - int(x) + w) * self.repFact, (y - int(y) + w) * self.repFact])
        h = self.repFact * (radius ** 2 + (length / 2.) ** 2) ** 0.5
        beta = num.arctan2(num.array(radius), num.array(length / 2.))

        x0=cx+num.array([num.cos(beta+ang),num.sin(beta+ang)])*h
        x1=cx+num.array([num.cos(ang-beta+num.pi),num.sin(ang-beta+num.pi)])*h
        x2=cx+num.array([num.cos(ang+beta+num.pi),num.sin(beta+ang+num.pi)])*h
        x3=cx+num.array([num.cos(ang-beta),num.sin(ang-beta)])*h


        map=num.zeros((A,B)).astype('float')
        #draw the box
        l0=line(x0,x1)
        l1=line(x1,x2)
        l2=line(x2,x3)
        l3=line(x3,x0)
        self.l0=l0
        self.l1=l1
        self.l2=l2
        self.l3=l3
        if abs(ang)%num.pi in [0,num.pi/2,num.pi,-num.pi/2,-num.pi]:
            corners=num.concatenate([[x0],[x1],[x2],[x3]])
            map[num.min(corners[:,1]).astype('int'):num.max(corners[:,1]).astype('int') , num.min(corners[:,0]).astype('int'):num.max(corners[:,0]).astype('int')]=1.
        else:
            perimeter=[]
            for ii in num.arange(l0.xlim[0],l0.xlim[1]):
                perimeter.append([ii+0.5,l0(ii)])
            for ii in num.arange(l1.xlim[0],l1.xlim[1]):
                perimeter.append([ii+0.5,l1(ii)])
            for ii in num.arange(l2.xlim[0],l2.xlim[1]):
                perimeter.append([ii+0.5,l2(ii)])
            for ii in num.arange(l3.xlim[0],l3.xlim[1]):
                perimeter.append([ii+0.5,l3(ii)])

            perimeter=num.array(perimeter).astype('int')
            ux=num.unique(perimeter[:,0])
            for ii in range(len(ux)):
                if (ux[ii]>=len(map[0,:])) or (ux[ii]<0): continue
                ww=num.where(perimeter[:,0]==ux[ii])
                y=perimeter[ww][:,1]
                map[num.max([0,num.min(y)]):num.max([0,num.max(y)+1]),ux[ii]]=1.
                #ADDIN double check for pixels beyond 1 of the lines.

        p0 = (cx + self.repFact *( length / 2.) *
              num.array([num.cos(ang), num.sin(ang)]))
        p1 = (cx + self.repFact * (length / 2.) *
              num.array([num.cos(ang + num.pi), num.sin(ang + num.pi)]))

        xeval=num.linspace(max(0.,p0[0]-radius*self.repFact),min(p0[0]+radius*self.repFact,B-1),radius*100*2)
        for ii in range(len(xeval)):
            val=(radius*self.repFact)**2-(xeval[ii]-p0[0])**2
            if val<0: continue
            y=val**0.5
            y0=-y+p0[1]+0.5
            y1=y+p0[1]+0.5
            if (y0<0) and (y1<0): continue
            if (y0<0): y0=0
            if int(y0)==int(y1): y1+=1
            map[int(y0):int(y1),int(xeval[ii]+0.5)]=1.

        xeval=num.linspace(max(0.,p1[0]-radius*self.repFact),min(p1[0]+radius*self.repFact,B-1),radius*100*2)
        for ii in range(len(xeval)):
            val=(radius*self.repFact)**2-(xeval[ii]-p1[0])**2
            if val<0: continue
            y=val**0.5
            y0=-y+p1[1]+0.5
            y1=y+p1[1]+0.5
            if (y0<0) and (y1<0): continue
            if (y0<0): y0=0
            if int(y0)==int(y1): y1+=1
            map[int(y0):int(y1),int(xeval[ii]+0.5)]=1.


        #print p0,p1,radius*self.repFact
        #print x0,x1,x2,x3

        self.mask=map*1.
        if retObj:
            return map*repData
        omap=num.equal(map,0.0)
        self.bgmask=omap*1.
        #pyl.imshow(omap*repData)
        #pyl.show()
        return omap*repData


        pyl.scatter(x0[0],x0[1],marker='^')
        pyl.scatter(x1[0],x1[1],marker='s')
        pyl.scatter(x2[0],x2[1])
        pyl.scatter(x3[0],x3[1])
        pyl.scatter(cx[0],cx[1])
        pyl.show()
        sys.exit()


def bgselect(event):
    """
    I don't think this is actually used. Haven't confirmed yet.
    """
    global CA
    print(CA.get_xlim())
    print(CA.get_ylim())



if __name__=="__main__":

    import pylab as pyl
    psfNoLine=modelPSF(num.arange(25),num.arange(25),alpha=1.5,beta=2.0,repFact=10)
    psfNoLine.writeto('noline.fits')
    print()
    psfLine=modelPSF(num.arange(25),num.arange(25),alpha=1.5,beta=2.0,repFact=10)
    psfLine.line(4.0,32.,0.45)
    psfLine.writeto('line.fits')
    sys.exit()
    (A,B)=psf.shape
    for i in range(A/2,A/2+1):
        pyl.plot(psf.x,psf.psf[i,:])
    for i in range(A*10/2,A*10/2+1):
        pyl.plot(psf.X,psf.PSF[i,:],linestyle=':')
    pyl.show()
