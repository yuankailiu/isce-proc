#!/usr/bin/env python

import os
import asf_search as asf

download = False

# Credential
with open(os.path.expanduser('~/.netrc'), 'r') as f:
    line = f.read().splitlines()[0]
    content = line.split()
    for i, ele in enumerate(content):
        if   ele == 'machine':  server   = content[i+1]
        elif ele == 'login':    username = content[i+1]
        elif ele == 'password': password = content[i+1]
    print(f'machine: {server}  user: {username}')

session = asf.ASFSession().auth_with_creds(username, password)


# SELECT AOI - edit point
wkt = 'POLYGON((32.0 26.7,40.0 26.7,40.0 34.0,32.0 34.0,32.0 26.7))'


# Search
search_results = asf.geo_search(
    platform        =   asf.SENTINEL1A,
    intersectsWith  =   wkt,
    start           =   '2014-01-01',
    end             =   '2023-06-01',
    processingLevel =   asf.SLC,
    beamMode        =   asf.IW,
    relativeOrbit   =   14,         # Change the path
    flightDirection =   asf.ASCENDING,
)

print(f'--Showing Results--')
for product in search_results:
    print(product.properties['fileName'])


if download:
    print(f'--Downloading Results--')
    search_results.download(
        path = './',
        session = session,
        processes = 10
    )
