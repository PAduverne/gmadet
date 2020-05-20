#! /usr/bin/env python
# -*- coding: utf-8 -*-

import os, subprocess
from astropy.io import fits
from astropy import wcs
from astropy.wcs import WCS
from astropy.coordinates import SkyCoord
from astropy import units as u
import numpy as np
from registration import registration
from ps1_survey import ps1_grid, prepare_PS1_sub
from utils import get_phot_cat, mkdir_p
import hjson
from psfex import psfex

def get_corner_coords(filename):
    """ Compute the RA, Dec coordinates at the corner of one image"""

    header = fits.getheader(filename)
    Naxis1 = header['NAXIS1']
    Naxis2 = header['NAXIS2']

    pix_coords = [[0,0,Naxis1,Naxis1], [0,Naxis2,Naxis2,0]]

    # Get physical coordinates of OT
    w = WCS(header)
    ra, dec = w.all_pix2world(pix_coords[0],pix_coords[1], 1)

    return [ra, dec]

def substraction(filenames, reference, config, soft='hotpants',
                 method='individual', verbose='NORMAL',outLevel=1):
    """Substract a reference image to the input image"""

    imagelist = np.atleast_1d(filenames)
    for ima in imagelist:
        # Create folder with substraction results
        path, filename = os.path.split(ima)
        if path:
            folder = path + '/'
        else:
            folder = ''

        resultDir = folder + 'substraction/'
        mkdir_p(resultDir)

        # Get coordinates of input image 
        im_coords = get_corner_coords(ima)

        # Define the reference image
        if reference == 'ps1':
            _, band, _ = get_phot_cat(ima, None)
            if band == 'B':
                band = 'g'
            elif band == 'V':
                band = 'g'
            elif band == 'R':
                band = 'r'
            elif band == 'I':
                band = 'i'
            elif band == 'g+r':
                band = 'r'
            #band = 'g'
            ps1_cell_table = ps1_grid(im_coords)
            # Get PS1 files with whom to perform substraction
            subfiles = prepare_PS1_sub(ps1_cell_table, band, ima, config, verbose=verbose, method=method)
            regis_info = registration(subfiles, config, resultDir=resultDir, verbose=verbose)
        
            if soft == 'hotpants':
                subFiles = hotpants(regis_info, config, verbose=verbose)

        # Delete files if necessary, mainly to save space disk
        # Problem when deleting files, they will appear in output files but
        # user can not have a look at some that might be important
        if outLevel == 0:
            #rm_p(ima)
            rm_p(refim)
            rm_p(refim_mask)
            rm_p(ima_regist)
            rm_p(refim_regist)
            rm_p(refim_regist_mask)

    return subFiles

def hotpants(regis_info, config, verbose='QUIET'):
    """Image substraction using hotpants"""
    
    if verbose == 'QUIET':
        verbosity = 0
    elif verbose == 'NORMAL':
        verbosity = 1
    elif verbose == 'FULL':
        verbosity = 2

    subfiles = []

    # Loop over the files 
    for info in regis_info:
        inim = info['inim']
        refim = info['refim']
        maskim = info['mask']

        path, filename = os.path.split(inim)
        if path:
           folder = path + '/'
        else:
           folder = ''

        resfile = inim.split('.')[0] + '_sub.fits'
        resmask = inim.split('.')[0] + '_sub_mask.fits'

        with open(config['hotpants']['conf']) as json_file: 
            hotpants_conf = hjson.load(json_file)

        if hotpants_conf['ng'] == 'auto' or hotpants_conf['r'] == 'auto' or hotpants_conf['rss'] == 'auto':
            # Compute PSF FWHM on input and ref images
            FWHM_inim = psfex(inim, config, verbose='QUIET')
            FWHM_refim = psfex(refim, config, verbose='QUIET')

        if hotpants_conf['ng'] == 'auto':
            # transfrom to sigma
            sigma_inim = FWHM_inim / (2*np.sqrt(2*np.log(2)))
            sigma_refim = FWHM_refim / (2*np.sqrt(2*np.log(2)))

            # As decribed here https://github.com/acbecker/hotpants
            kernel_match = np.sqrt(sigma_inim**2 - sigma_refim**2)
    
            # update config file for hotpants
            hotpants_conf['ng'] = '3 6 %.2f 4 %.2f 2 %.2f' % (0.5*kernel_match, kernel_match, 2*kernel_match)

        if hotpants_conf['r'] == 'auto':
            # Same as DECAM, arbitray
            hotpants_conf['r'] = str(int(FWHM_inim[0] * 2.5))
        if hotpants_conf['rss'] == 'auto':
            # Same as DECAM, arbitray
            hotpants_conf['rss'] = str(int(FWHM_inim[0] * 5))

        # Set min and max acceptable values for input and template images
        # Too simple, need to adapt it in the future
        il = str(info['in_lo'])
        iu = str(info['in_up'])
        tl = str(info['ref_lo'])
        tu = str(info['ref_up'])
        overlap = '%s, %s, %s, %s' % (info['XY_lim'][0],
                                      info['XY_lim'][1],
                                      info['XY_lim'][2],
                                      info['XY_lim'][3])

        hotpants_cmd = 'hotpants -inim %s -tmplim %s -outim %s -omi %s ' % (inim, refim, resfile, resmask)
        #hotpants_cmd += '-il %s -iu %s -tl %s -tu %s -gd %s ' % (il, iu, tl, tu, overlap)
        hotpants_cmd += '-il %s -iu %s -tl %s -tu %s ' % (il, iu, tl, tu)
        hotpants_cmd += '-tuk %s -iuk %s ' % (tu, iu)
        hotpants_cmd += '-ig %s -tg %s ' % (info['gain_in'], info['gain_ref'])
        hotpants_cmd += '-v %s ' % verbosity

        if maskim:
            hotpants_cmd += '-tmi %s ' % maskim

        # Add params from the hjson conf file
        for key, value in hotpants_conf.items():
            hotpants_cmd += '-%s %s ' % (key, value)

        hotpants_cmd_file = path + filename.split('.')[0] + '_hotpants.sh'
        os.system("echo %s > %s" % (hotpants_cmd, hotpants_cmd_file))
    
        os.system(hotpants_cmd)
        #subprocess.call([hotpants_cmd])
    
        # Set bad pixel values to 0 and others to 1 for sextractor
        hdulist = fits.open(resmask)
        hdulist[0].data[hdulist[0].data == 0] = 1
        hdulist[0].data[hdulist[0].data != 1] = 0
        hdulist.writeto(resmask, overwrite=True)

        subfiles.append([inim, refim, resfile, resmask])

    return subfiles
