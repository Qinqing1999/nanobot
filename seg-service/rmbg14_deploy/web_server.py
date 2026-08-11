"""
RMBG-1.4 ONNX 背景去除 Web 服务 (动态分辨率, 内存优化)

用法:
    python web_server.py                     # 默认 640x640, 适合 2G/2核
    python web_server.py --size 1024         # 高精度 1024x1024
    python web_server.py --size 768 --port 8080
"""
import os, io, time, base64
import numpy as np
from PIL import Image
import onnxruntime as ort

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

# 命令行参数
import argparse
_p = argparse.ArgumentParser()
_p.add_argument('--size', type=int, default=640, choices=[640, 768, 1024],
                help='输入分辨率: 640(低内存~361MB) / 768(平衡~419MB) / 1024(高精度~558MB)')
_p.add_argument('--port', type=int, default=5000)
_p.add_argument('--host', default='0.0.0.0')
_args = _p.parse_args()

INPUT_SIZE = _args.size
if INPUT_SIZE == 1024:
    MODEL_PATH = os.path.join(MODEL_DIR, "model.onnx")
else:
    MODEL_PATH = os.path.join(MODEL_DIR, "model_dynamic.onnx")

print(f"[RMBG-1.4] 加载模型: {MODEL_PATH}")
print(f"[RMBG-1.4] 模型大小: {os.path.getsize(MODEL_PATH)/1048576:.1f} MB")
print(f"[RMBG-1.4] 输入分辨率: {INPUT_SIZE}x{INPUT_SIZE}")
so = ort.SessionOptions()
so.enable_cpu_mem_arena = False  # 关闭内存竞技场, 大幅降低峰值内存
so.intra_op_num_threads = 2      # 2 线程, 适合双核
sess = ort.InferenceSession(MODEL_PATH, so, providers=["CPUExecutionProvider"])
input_name = sess.get_inputs()[0].name
print(f"[RMBG-1.4] 输入: {input_name} shape={sess.get_inputs()[0].shape}")
print(f"[RMBG-1.4] 加载完成!\n")

def preprocess(pil_img):
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    ow, oh = pil_img.size
    r = pil_img.resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
    a = np.array(r).astype(np.float32) / 255.0
    a = (a - 0.5) / 0.5
    a = a.transpose(2, 0, 1)[None, ...]
    return a, (ow, oh)

def postprocess(result, orig_size):
    ow, oh = orig_size
    m = result[0, 0] if result.ndim == 4 else (result[0] if result.ndim == 3 else result)
    mp = Image.fromarray(m.astype(np.float32), mode='F').resize((ow, oh), Image.BILINEAR)
    ma = np.array(mp)
    mx, mn = ma.max(), ma.min()
    if mx > mn:
        ma = (ma - mn) / (mx - mn)
    return (ma * 255).astype(np.uint8)

def predict(pil_img):
    inp, osz = preprocess(pil_img)
    t0 = time.time()
    out = sess.run(None, {input_name: inp})
    ms = (time.time() - t0) * 1000
    mask = postprocess(out[0], osz)
    rgba = pil_img.convert("RGBA").copy()
    rgba.putalpha(Image.fromarray(mask, mode='L'))
    return rgba, mask, ms

from flask import Flask, request, jsonify

app = Flask(__name__)

HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RMBG-1.4 背景去除</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f0f0f;color:#e0e0e0;min-height:100vh;display:flex;flex-direction:column;align-items:center}
.header{width:100%;background:linear-gradient(135deg,#1a1a2e,#16213e);padding:24px 0;text-align:center;border-bottom:1px solid #333}
.header h1{font-size:28px;font-weight:700;color:#fff}
.header p{font-size:14px;color:#888;margin-top:6px}
.container{max-width:1200px;width:100%;padding:32px 20px;display:flex;flex-direction:column;align-items:center}
.upload-area{display:block;width:100%;max-width:600px;border:2px dashed #444;border-radius:16px;padding:48px 24px;text-align:center;cursor:pointer;transition:all .3s;background:#1a1a1a}
.upload-area:hover{border-color:#4a9eff;background:#1e2a3a}
.upload-area.dragover{border-color:#4a9eff;background:#1e2a3a}
.upload-area .icon{font-size:48px;margin-bottom:16px}
.upload-area .text{font-size:16px;color:#aaa}
.upload-area .hint{font-size:12px;color:#666;margin-top:8px}
#fi{position:absolute;width:0;height:0;opacity:0;overflow:hidden}
.btn{background:#4a9eff;color:#fff;border:none;padding:12px 32px;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer;transition:all .3s;margin-top:20px}
.btn:hover{background:#3a8eef}
.btn-dl{background:#28a745}
.btn-dl:hover{background:#218838}
.results{display:none;width:100%;max-width:1000px;margin-top:32px}
.results.show{display:block}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;margin-bottom:24px}
@media(max-width:768px){.grid{grid-template-columns:1fr}}
.card{background:#1a1a1a;border-radius:12px;overflow:hidden;border:1px solid #333}
.card .label{padding:10px 16px;font-size:13px;font-weight:600;color:#888;background:#181818;border-bottom:1px solid #2a2a2a}
.card .iw{padding:16px;display:flex;justify-content:center;align-items:center;min-height:200px}
.card img{max-width:100%;max-height:400px;border-radius:8px}
.cb{background-image:linear-gradient(45deg,#2a2a2a 25%,transparent 25%),linear-gradient(-45deg,#2a2a2a 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#2a2a2a 75%),linear-gradient(-45deg,transparent 75%,#2a2a2a 75%);background-size:20px 20px;background-position:0 0,0 10px,10px -10px,-10px 0}
.info-bar{display:flex;justify-content:center;gap:24px;padding:16px;background:#1a1a1a;border-radius:10px;margin-bottom:16px;flex-wrap:wrap}
.info-item{text-align:center}
.info-item .v{font-size:20px;font-weight:700;color:#4a9eff}
.info-item .l{font-size:12px;color:#666;margin-top:2px}
.loading{display:none;text-align:center;padding:40px}
.loading.show{display:block}
.spinner{width:48px;height:48px;border:4px solid #333;border-top:4px solid #4a9eff;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 16px}
@keyframes spin{to{transform:rotate(360deg)}}
.error{display:none;background:#2a1010;border:1px solid #f44;color:#f88;padding:16px;border-radius:8px;margin-top:16px;text-align:center}
.error.show{display:block}
.actions{display:flex;justify-content:center;gap:12px;margin-top:16px}
</style>
</head>
<body>
<div class="header">
<h1>RMBG-1.4 背景去除工具</h1>
<p>FP32 | __SIZE__ | ONNX Runtime CPU | 内存优化</p>
</div>
<div class="container">
<label class="upload-area" id="ua" for="fi">
<div class="icon">📷</div>
<div class="text">点击或拖拽图片到此处上传</div>
<div class="hint">支持 JPG / PNG / WEBP</div>
</label>
<input type="file" id="fi" accept="image/*" onchange="hf(this.files[0])">
<div class="loading" id="ld"><div class="spinner"></div><p>正在处理图片...</p></div>
<div class="error" id="er"></div>
<div class="results" id="rs">
<div class="info-bar" id="ib"></div>
<div class="grid">
<div class="card"><div class="label">原始图片</div><div class="iw"><img id="io"></div></div>
<div class="card"><div class="label">前景 Mask</div><div class="iw"><img id="im"></div></div>
<div class="card"><div class="label">去除背景 (透明 PNG)</div><div class="iw cb"><img id="ir"></div></div>
</div>
<div class="actions">
<button class="btn btn-dl" onclick="dl()">下载透明 PNG</button>
<button class="btn" onclick="rst()">上传新图片</button>
</div>
</div>
</div>
<script>
let rd=null;
const ua=document.getElementById('ua'),
      fi=document.getElementById('fi'),
      ld=document.getElementById('ld'),
      er=document.getElementById('er'),
      rs=document.getElementById('rs');

ua.addEventListener('dragover',e=>{e.preventDefault();ua.classList.add('dragover')});
ua.addEventListener('dragleave',()=>ua.classList.remove('dragover'));
ua.addEventListener('drop',e=>{
    e.preventDefault();
    ua.classList.remove('dragover');
    if(e.dataTransfer.files.length) hf(e.dataTransfer.files[0]);
});

function hf(f){
    if(!f) return;
    if(!f.type.startsWith('image/')){se('请上传图片文件');return}
    var reader=new FileReader();
    reader.onload=function(e){
        document.getElementById('io').src=e.target.result;
    };
    reader.readAsDataURL(f);
    rs.classList.remove('show');
    er.classList.remove('show');
    ld.classList.add('show');
    var fd=new FormData();
    fd.append('image',f);
    fetch('/predict',{method:'POST',body:fd})
    .then(function(r){return r.json()})
    .then(function(d){
        ld.classList.remove('show');
        if(d.error){se(d.error)}else{sr(d)}
    })
    .catch(function(){
        ld.classList.remove('show');
        se('网络错误，请重试');
    });
}

function sr(d){
    rd=d;
    // 用服务器返回的原图，不覆盖已有显示
    if(d.original){
        document.getElementById('io').src='data:image/png;base64,'+d.original;
    }
    document.getElementById('im').src='data:image/png;base64,'+d.mask;
    document.getElementById('ir').src='data:image/png;base64,'+d.result;
    document.getElementById('ib').innerHTML=
        '<div class="info-item"><div class="v">'+d.width+'x'+d.height+'</div><div class="l">图片尺寸</div></div>'+
        '<div class="info-item"><div class="v">'+d.inference_ms+' ms</div><div class="l">推理耗时</div></div>'+
        '<div class="info-item"><div class="v">'+d.mask_mean+' / 255</div><div class="l">Mask均值</div></div>'+
        '<div class="info-item"><div class="v">FP32 ' + d.size + '</div><div class="l">模型</div></div>';
    rs.classList.add('show');
}

function se(m){er.textContent=m;er.classList.add('show')}
function dl(){
    if(!rd) return;
    var a=document.createElement('a');
    a.href='data:image/png;base64,'+rd.result;
    a.download='no_bg.png';
    a.click();
}
function rst(){
    rs.classList.remove('show');
    er.classList.remove('show');
    fi.value='';
    ua.scrollIntoView({behavior:'smooth'});
}
</script>
</body>
</html>'''
HTML = HTML.replace('__SIZE__', f'{INPUT_SIZE}x{INPUT_SIZE}')

@app.route('/')
def index():
    return HTML

@app.route('/predict', methods=['POST'])
def predict_route():
    if 'image' not in request.files:
        return jsonify({'error': '没有上传图片'})
    f = request.files['image']
    if f.filename == '':
        return jsonify({'error': '没有选择文件'})
    try:
        img = Image.open(f.stream)
        w, h = img.size
        rgba, mask, ms = predict(img)

        # 编码原始图片 (缩放到 800px 以减少传输)
        orig_small = img.convert('RGB').copy()
        if max(w, h) > 800:
            orig_small.thumbnail((800, 800), Image.BILINEAR)
        buf0 = io.BytesIO()
        orig_small.save(buf0, format='JPEG', quality=85)
        orig_b64 = base64.b64encode(buf0.getvalue()).decode()

        # 编码 mask
        buf1 = io.BytesIO()
        Image.fromarray(mask, mode='L').save(buf1, format='PNG')

        # 编码结果
        buf2 = io.BytesIO()
        rgba.save(buf2, format='PNG')

        return jsonify({
            'original': orig_b64,
            'mask': base64.b64encode(buf1.getvalue()).decode(),
            'result': base64.b64encode(buf2.getvalue()).decode(),
            'width': w, 'height': h,
            'inference_ms': int(ms),
            'mask_mean': round(float(mask.mean()), 1),
            'size': INPUT_SIZE,
        })
    except Exception as e:
        return jsonify({'error': str(e)})

if __name__ == '__main__':
    print(f"\n{'='*50}")
    print(f"  RMBG-1.4 Web 服务已启动")
    print(f"  地址: http://localhost:{_args.port}")
    print(f"  模型: FP32 {INPUT_SIZE}x{INPUT_SIZE}")
    print(f"  峰值内存: ~{360 if INPUT_SIZE==640 else 420 if INPUT_SIZE==768 else 560} MB")
    print(f"{'='*50}\n")
    app.run(host=_args.host, port=_args.port, debug=False)
