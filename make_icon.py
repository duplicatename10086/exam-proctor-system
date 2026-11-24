"""把用户给的头像转成 .ico 供母体/子体使用。"""
from PIL import Image

src = r"C:\Users\zyx\Desktop\大肥鱼群星mod开发\素材 (7).jpg"
out = r"C:\Users\zyx\Documents\deepseek-harness\default-workspace\proctor-system\assets\icon.ico"
import os
os.makedirs(os.path.dirname(out), exist_ok=True)
im = Image.open(src).convert("RGBA")
# 圆角/透明背景处理：白底转透明
d = im.getdata()
new = []
for r, g, b, a in d:
    if r > 235 and g > 235 and b > 235:
        new.append((r, g, b, 0))
    else:
        new.append((r, g, b, a))
im.putdata(new)
im.save(out, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("saved", out, os.path.getsize(out), "bytes")
