from __future__ import annotations

"""Create a self-contained, standard-library-generated PowerPoint test deck."""

import argparse
import html
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

EMU = 914400
SW, SH = 12192000, 6858000  # 13.333 x 7.5 in, 16:9 widescreen
NS_A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
NS_P = 'http://schemas.openxmlformats.org/presentationml/2006/main'


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def xfrm(x, y, w, h):
    return f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>'


def shape_xml(shape_id, name, x, y, w, h, text='', *, fill='none', line='none', size=2400,
              color='172B4D', bold=False, align='l', geom='rect', margin=91440):
    fill_xml = '<a:noFill/>' if fill == 'none' else f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>'
    line_xml = '<a:ln><a:noFill/></a:ln>' if line == 'none' else f'<a:ln w="19050"><a:solidFill><a:srgbClr val="{line}"/></a:solidFill></a:ln>'
    paras = []
    for row in text.split('\n'):
        props = f'<a:pPr algn="{align}"/>'
        run = '' if not row else f'<a:r><a:rPr lang="en-US" sz="{size}" b="{1 if bold else 0}"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:latin typeface="Aptos"/></a:rPr><a:t>{esc(row)}</a:t></a:r>'
        paras.append(f'<a:p>{props}{run}<a:endParaRPr lang="en-US"/></a:p>')
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{esc(name)}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr>{xfrm(x,y,w,h)}<a:prstGeom prst="{geom}"><a:avLst/></a:prstGeom>{fill_xml}{line_xml}</p:spPr>'
            f'<p:txBody><a:bodyPr wrap="square" lIns="{margin}" rIns="{margin}" tIns="{margin}" bIns="{margin}"/><a:lstStyle/>{"".join(paras)}</p:txBody></p:sp>')


def slide_xml(shapes):
    tree = '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:a="{NS_A}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="{NS_P}"><p:cSld><p:spTree>{tree}{"".join(shapes)}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'


def notes_xml(note: str):
    # Notes placeholders are deliberately simple text shapes; notes are linked to their slide by relationships.
    return slide_xml([shape_xml(2, 'Speaker Notes', 800000, 1000000, 10500000, 4500000,
                                note, size=1800, color='333333')]).replace('<p:sld ', '<p:notes ', 1).replace('</p:sld>', '</p:notes>')


def rels(items):
    body = ''.join(f'<Relationship Id="{rid}" Type="{typ}" Target="{target}"/>' for rid, typ, target in items)
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{body}</Relationships>'


def build_parts():
    navy, blue, teal, orange, pale = '12304A', '2F80ED', '12B8A6', 'F2994A', 'EEF4F8'
    slides = []
    notes = []
    def add(items, note):
        slides.append(items); notes.append(note)
    def title(t, subtitle='', n=2):
        items = [shape_xml(n, 'Title', 700000, 420000, 10800000, 900000, t, size=3000, color=navy, bold=True)]
        if subtitle: items.append(shape_xml(n+1, 'Subtitle', 720000, 1320000, 10500000, 560000, subtitle, size=1450, color='53677A'))
        return items

    # 1 — title
    add([shape_xml(2,'Accent bar',0,0,250000,SH,fill=blue),
         shape_xml(3,'Title',850000,1550000,10500000,1100000,'IT Talent Hunt',size=4400,color=navy,bold=True),
         shape_xml(4,'Subtitle',880000,2800000,9500000,650000,'A practical test presentation for live slide control',size=2200,color='53677A'),
         shape_xml(5,'Tag',880000,4800000,4800000,500000,'DEMO DECK  •  8 SLIDES',size=1300,color=blue,bold=True)],
        'Welcome the audience. This deck is designed to exercise common presentation layouts and live annotation controls.')
    # 2 agenda
    items=title('Today’s route','A short tour of the people, skills, and process behind a strong technology team.')
    for i,(num,lab,desc) in enumerate([('01','Talent landscape','Roles and capabilities'),('02','Skills in action','How the team delivers'),('03','Hiring signals','Evidence that matters'),('04','Next steps','A focused 30-day plan')]):
        y=2250000+i*880000
        items += [shape_xml(10+i*2,'Agenda number',900000,y,800000,520000,num,size=1900,color=blue,bold=True),shape_xml(11+i*2,'Agenda item',1950000,y,7800000,560000,lab+'  —  '+desc,size=1800,color=navy)]
    add(items,'Preview the four sections. The agenda is intentionally text-heavy to test selection and navigation.')
    # 3 text slide
    items=title('The talent landscape','A clear role definition helps teams hire for outcomes, not keyword matches.')
    items += [shape_xml(10,'Body',900000,2300000,6500000,2800000,'Product-minded engineers\nCloud and platform specialists\nData practitioners\nSecurity champions',size=2200,color=navy),
              shape_xml(11,'Callout',8200000,2350000,3000000,2100000,'Strong teams combine\ncraft, curiosity,\nand collaboration.',fill=pale,line='D7E2EA',size=2100,color=teal,bold=True,align='ctr')]
    add(items,'Emphasize that role descriptions should describe measurable outcomes, collaboration, and learning—not just tools.')
    # 4 vector art
    items=title('Skills work together','A simple capability map, built from editable PowerPoint shapes.')
    items += [shape_xml(10,'Product',4300000,2300000,3000000,750000,'PRODUCT',fill=navy,size=1800,color='FFFFFF',bold=True,align='ctr'),
              shape_xml(11,'Engineering',900000,3900000,3200000,850000,'ENGINEERING',fill=blue,size=1800,color='FFFFFF',bold=True,align='ctr'),
              shape_xml(12,'Data',4500000,4350000,2800000,850000,'DATA',fill=teal,size=1800,color='FFFFFF',bold=True,align='ctr'),
              shape_xml(13,'Security',8200000,3900000,3200000,850000,'SECURITY',fill=orange,size=1800,color='FFFFFF',bold=True,align='ctr'),
              shape_xml(14,'Center',5300000,3200000,800000,800000,'+',geom='ellipse',fill='FFFFFF',line='B9C9D6',size=2400,color=navy,bold=True,align='ctr')]
    # connectors represented as thin editable rectangles
    items += [shape_xml(15,'Connector A',5200000,3040000,200000,800000,fill='B9C9D6'),shape_xml(16,'Connector B',3800000,3540000,1500000,180000,fill='B9C9D6'),shape_xml(17,'Connector C',6800000,3540000,1600000,180000,fill='B9C9D6'),shape_xml(18,'Connector D',5600000,3950000,180000,420000,fill='B9C9D6')]
    add(items,'Use the diagram to explain shared ownership. Each colored block and connector is a native vector shape that can be selected or annotated over.')
    # 5 chart-like
    items=title('A balanced skills portfolio','Illustrative team capability score (out of 10).')
    cats=[('Cloud',8),('Data',7),('Security',6),('Product',8)]
    for i,(lab,val) in enumerate(cats):
        y=2350000+i*760000
        items += [shape_xml(10+i*2,'Category',1000000,y,1800000,400000,lab,size=1600,color=navy),
                  shape_xml(11+i*2,'Bar',2900000,y,600000*val,420000,'',fill=[blue,teal,orange,navy][i]),
                  shape_xml(20+i,'Score',8000000,y,800000,420000,str(val),size=1600,color=navy,bold=True)]
    items += [shape_xml(30,'Scale',2900000,5550000,6000000,380000,'0       2       4       6       8       10',size=1200,color='53677A')]
    add(items,'The bars show a sample self-assessment, not measured hiring data. Ask where mentoring or hiring would most improve balance.')
    # 6 table-like grid
    items=title('Hiring signals to look for','Use the same evidence framework across interviews.')
    rows=[('Signal','What it looks like','Evidence'),('Problem solving','Frames trade-offs','Clear reasoning'),('Ownership','Closes the loop','Shipped outcomes'),('Teamwork','Shares context','Useful feedback'),('Learning','Adapts quickly','Specific reflection')]
    x0,y0=850000,2200000; widths=[2700000,4200000,3900000]; rh=650000
    for ri,row in enumerate(rows):
        x=x0
        for ci,text in enumerate(row):
            fill=navy if ri==0 else ('EEF4F8' if ri%2 else 'FFFFFF')
            items.append(shape_xml(10+ri*3+ci,'Table cell',x,y0+ri*rh,widths[ci],rh,text,fill=fill,line='D7E2EA',size=1450,color='FFFFFF' if ri==0 else navy,bold=ri==0))
            x += widths[ci]
    add(items,'Invite interviewers to record concrete examples against each signal. The table is composed of editable native shapes for compatibility.')
    # 7 notes / section break
    add([shape_xml(2,'Section marker',850000,1050000,2000000,500000,'FIELD NOTES',size=1400,color=teal,bold=True),
         shape_xml(3,'Title',850000,1900000,10500000,1100000,'Make the work visible.',size=3900,color=navy,bold=True),
         shape_xml(4,'Body',900000,3450000,9000000,1000000,'Show candidates the real problems, the team rituals, and the room to grow.',size=2300,color='53677A')],
        'Section break: give candidates a realistic view of the work and the support available. Speaker notes are included on every slide.')
    # 8 closing
    add([shape_xml(2,'Closing panel',0,0,SW,SH,fill=navy),
         shape_xml(3,'Closing title',900000,1700000,10500000,1050000,'Build the team that builds what’s next.',size=3500,color='FFFFFF',bold=True),
         shape_xml(4,'Closing subtext',940000,3150000,9000000,700000,'Clear roles. Strong signals. Better outcomes.',size=2100,color='C8D8E5'),
         shape_xml(5,'Closing mark',940000,5000000,2600000,450000,'THANK YOU',size=1500,color='12B8A6',bold=True)],
        'Close by inviting questions and discussion. Thank the audience for their time.')

    parts={}
    parts['[Content_Types].xml'] = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/><Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/><Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/><Override PartName="/ppt/notesMasters/notesMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"/>' + ''.join(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/><Override PartName="/ppt/notesSlides/notesSlide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>' for i in range(1,9)) + '</Types>'
    parts['_rels/.rels']=rels([('rId1','http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument','ppt/presentation.xml')])
    slide_ids = ''.join('<p:sldId id="{}" r:id="rId{}"/>'.format(255+i, i+1) for i in range(8))
    parts['ppt/presentation.xml']=f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:presentation xmlns:a="{NS_A}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="{NS_P}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:notesMasterIdLst><p:notesMasterId r:id="rId10"/></p:notesMasterIdLst><p:sldIdLst>{slide_ids}</p:sldIdLst><p:sldSz cx="{SW}" cy="{SH}" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
    parts['ppt/_rels/presentation.xml.rels']=rels([('rId1','http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster','slideMasters/slideMaster1.xml')]+[(f'rId{i+1}','http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide',f'slides/slide{i+1}.xml') for i in range(8)]+[('rId10','http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster','notesMasters/notesMaster1.xml')])
    parts['ppt/slideLayouts/slideLayout1.xml']=f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldLayout xmlns:a="{NS_A}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="{NS_P}" type="blank" preserve="1"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    parts['ppt/slideLayouts/_rels/slideLayout1.xml.rels']=rels([('rId1','http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster','../slideMasters/slideMaster1.xml')])
    parts['ppt/slideMasters/slideMaster1.xml']=f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldMaster xmlns:a="{NS_A}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="{NS_P}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMap accent1="{blue}" accent2="{teal}" accent3="{orange}" accent4="4472C4" accent5="70AD47" accent6="A5A5A5" bg1="FFFFFF" bg2="E7E6E6" dk1="000000" dk2="44546A" folHlink="0563C1" hlink="0563C1" lt1="FFFFFF" lt2="E7E6E6"/><p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>'
    parts['ppt/slideMasters/_rels/slideMaster1.xml.rels']=rels([('rId1','http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout','../slideLayouts/slideLayout1.xml'),('rId2','http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme','../theme/theme1.xml')])
    parts['ppt/theme/theme1.xml']=f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:theme xmlns:a="{NS_A}" name="IT Talent"><a:themeElements><a:clrScheme name="IT Talent"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="12304A"/></a:dk2><a:lt2><a:srgbClr val="EEF4F8"/></a:lt2><a:accent1><a:srgbClr val="2F80ED"/></a:accent1><a:accent2><a:srgbClr val="12B8A6"/></a:accent2><a:accent3><a:srgbClr val="F2994A"/></a:accent3><a:accent4><a:srgbClr val="4472C4"/></a:accent4><a:accent5><a:srgbClr val="70AD47"/></a:accent5><a:accent6><a:srgbClr val="A5A5A5"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme><a:fontScheme name="Aptos"><a:majorFont><a:latin typeface="Aptos Display"/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/></a:minorFont></a:fontScheme><a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>'
    parts['ppt/notesMasters/notesMaster1.xml']=f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:notesMaster xmlns:a="{NS_A}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="{NS_P}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMap accent1="2F80ED" accent2="12B8A6" accent3="F2994A" accent4="4472C4" accent5="70AD47" accent6="A5A5A5" bg1="FFFFFF" bg2="EEF4F8" dk1="000000" dk2="12304A" folHlink="954F72" hlink="0563C1" lt1="FFFFFF" lt2="EEF4F8"/><p:notesStyle/></p:notesMaster>'
    parts['ppt/notesMasters/_rels/notesMaster1.xml.rels']=rels([('rId1','http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme','../theme/theme1.xml')])
    for i,shapes in enumerate(slides,1):
        parts[f'ppt/slides/slide{i}.xml']=slide_xml(shapes)
        parts[f'ppt/slides/_rels/slide{i}.xml.rels']=rels([('rId1','http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout','../slideLayouts/slideLayout1.xml'),('rId2','http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide',f'../notesSlides/notesSlide{i}.xml')])
        parts[f'ppt/notesSlides/notesSlide{i}.xml']=notes_xml(notes[i-1])
        parts[f'ppt/notesSlides/_rels/notesSlide{i}.xml.rels']=rels([('rId1','http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster','../notesMasters/notesMaster1.xml'),('rId2','http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide',f'../slides/slide{i}.xml')])
    return parts


def create_deck(output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, data in build_parts().items():
            archive.writestr(name, data.encode('utf-8'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', nargs='?', type=Path, default=Path(__file__).with_name('ppt_controller_test_deck.pptx'))
    args=parser.parse_args()
    create_deck(args.output)
    print(f'Created {args.output.resolve()}')

if __name__=='__main__':
    main()
