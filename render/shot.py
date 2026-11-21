import subprocess,time,sys
from playwright.sync_api import sync_playwright
srv=subprocess.Popen(['python3','-m','http.server','8765'],cwd='.',stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);time.sleep(1)
try:
  with sync_playwright() as p:
    b=p.chromium.launch(args=['--use-gl=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist']);pg=b.new_page(viewport={'width':1400,'height':1000})
    pg.on('console',lambda m:print(m.text)); pg.on('pageerror',lambda e:print('ERR',e))
    for m in ['outside','cutaway']:
      pg.goto(f'http://localhost:8765/scene.html?mode={m}');pg.wait_for_function('window.done',timeout=60000);pg.locator('#c').screenshot(path=f'hob_{m}.png')
    b.close()
finally: srv.terminate()
