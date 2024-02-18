import os
import glob
import xml.etree.ElementTree as ET

def get_length_xml(feed):
    tree = ET.parse(feed)
    root = tree.getroot()
    devices = root.findall("property")

    for device in devices:
        if device.get("name") == 'length':
            for ele in device:
                if ele.tag == 'value':
                    length = int(ele.text)
    return length


path = './secondarys'

for swath in ['IW1', 'IW2', 'IW3']:

    paths = os.path.join(path, '*', f'{swath}/burst*.slc.xml')
    files = sorted(glob.glob(paths))

    bursts = []
    for f in files:
        bursts.append(os.path.basename(f).split('.')[0])
    bursts = sorted(list(set(bursts)))

    dataDic = {}
    for f in files:
        date  = f.split('/')[-3]
        burst = os.path.basename(f).split('.')[0]
        n     = get_length_xml(f)
        tmp   = f'{n} {date}'
        if burst not in dataDic:
            dataDic[burst] = [tmp]
        else:
            dataDic[burst].append(tmp)

    for burst, info in dataDic.items():
        dataDic[burst] = sorted(info)

    with open(f'{swath}_burstlines.txt', 'w') as f:
        for burst, info in dataDic.items():
            f.write('#### '+burst+'\n')
            for line in info:
                f.write(line+'\n')

    print('finished')
