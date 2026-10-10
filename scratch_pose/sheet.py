import sys; sys.path.insert(0,"../src")
from PIL import Image, ImageDraw
from manganation.pose.library import load_library, compose
from manganation.pose.draw import draw_figures
lib=load_library()
tiles=[]
W,H=240,360
def tile(label, refs, boxes):
    im=draw_figures(compose(refs,boxes,W,H),W,H); d=ImageDraw.Draw(im); d.text((4,2),label,fill="white"); tiles.append(im)
for pid in lib["poses"]:
    tile(pid, {"a":pid}, {"a":(0.1,0.06,0.8,0.9)})
tile("drag_by_wrist", {"a":"drag_by_wrist.lead","b":"drag_by_wrist.follow"}, {})
tile("stand_over", {"a":"stand_over.above","b":"stand_over.below"}, {})
tile("drag@mirror", {"a":"drag_by_wrist.lead@mirror","b":"drag_by_wrist.follow@mirror"}, {})
cols=6; rows=(len(tiles)+cols-1)//cols
sheet=Image.new("RGB",(W*cols,H*rows))
for i,t in enumerate(tiles): sheet.paste(t,((i%cols)*W,(i//cols)*H))
sheet.save("out/sheet.png"); print(len(tiles))
