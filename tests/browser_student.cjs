const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1365,height:950}});
 const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 const base='http://127.0.0.1:8765';
 await page.goto(base);
 await page.fill('#nameInput','Иванов Алексей'); await page.click('#joinBtn');
 await page.locator('#statePanel:not([hidden])').waitFor();
 const teacher=await browser.newPage(); await teacher.goto(base+'/teacher');
 await teacher.evaluate(async()=>{window.control=new WebSocket('ws://127.0.0.1:8765/ws/teacher'); await new Promise(r=>control.onopen=r)});
 const gift='::Вопрос о сети::Какой протокол используется для безопасной передачи данных между браузером и веб-сервером? {=HTTPS ~FTP ~HTTP ~SMTP}\n\n::Верно или неверно::Земля вращается вокруг Солнца. {TRUE}\n\n::Короткий ответ::Столица России? {=Москва}\n\n::Несколько ответов::Выберите чётные числа {=2 =4 ~3}';
 const res=await page.request.post(base+'/api/upload_test',{multipart:{file:{name:'ui.gift',mimeType:'text/plain',buffer:Buffer.from(gift)}}});
 if(!res.ok())throw new Error(await res.text());
 await teacher.evaluate(()=>control.send(JSON.stringify({type:'start_test',force:true,question_seconds:60})));
 await page.locator('#questionPanel:not([hidden])').waitFor();
 if(!await page.locator('#sendBtn').isDisabled())throw new Error('Empty answer enabled');
 await page.locator('.option').first().click();

 await page.setViewportSize({width:390,height:844});

 if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw new Error('Mobile horizontal overflow');
 await page.click('#sendBtn');
 // Ответ принят — следующий вопрос открывается сразу, без команды преподавателя.
 await page.getByText('Земля вращается вокруг Солнца.',{exact:true}).waitFor();
 if(!await page.getByText('Вопрос 2 из 4',{exact:true}).isVisible())throw new Error('Immediate advance lost');
 const token=await page.evaluate(()=>sessionStorage.getItem('student_id'));
 await page.reload();
 await page.getByText('Земля вращается вокруг Солнца.',{exact:true}).waitFor();
 if(token!==await page.evaluate(()=>sessionStorage.getItem('student_id')))throw new Error('Token lost');
 await page.getByText('Верно',{exact:true}).click(); await page.click('#sendBtn');
 await page.getByText('Столица России?',{exact:true}).waitFor();
 await page.locator('#shortAnswerInput').waitFor();
 await page.fill('#shortAnswerInput','Москва'); await page.click('#sendBtn');
 await page.getByText('Выберите чётные числа',{exact:true}).waitFor();
 if(await page.locator('#options input[type=checkbox]').count()!==3)throw new Error('Multiple choice controls missing');
 await page.locator('.option').nth(0).click(); await page.locator('.option').nth(1).click();
 await page.click('#sendBtn');
 await page.getByText('Вы закончили',{exact:true}).waitFor();
 await page.reload(); await page.getByText('Вы закончили',{exact:true}).waitFor();
 const results=await (await page.request.get(base+'/api/results')).json();
 if(results.results[0].correct!==4)throw new Error(JSON.stringify(results));
 // Имя файла проверяем по заголовкам ответа: headless Chromium отбрасывает
 // кириллицу в атрибуте download и называет blob-файл просто «download».
 const report=await teacher.request.get(base+'/api/results/report.docx');
 if(!report.ok())throw new Error(await report.text());
 const disposition=report.headers()['content-disposition']||'';
 if(!disposition.includes('.docx') || !decodeURIComponent(disposition).includes('ui'))throw new Error(disposition);
 const downloadPromise=teacher.waitForEvent('download');
 await teacher.click('#exportReportBtn');
 const download=await downloadPromise;
 const reportFile=require('fs').readFileSync(await download.path());
 if(reportFile.subarray(0,2).toString()!=='PK')throw new Error('DOCX must be a zip archive');

 await teacher.evaluate(()=>control.send(JSON.stringify({type:'reset'})));
 await page.getByText('Ждём начала тестирования',{exact:true}).waitFor();
 const timedGift='::Таймер 1::Первый вопрос {TRUE}\n\n::Таймер 2::Второй вопрос {FALSE}';
 const timedUpload=await page.request.post(base+'/api/upload_test',{multipart:{file:{name:'timer.gift',mimeType:'text/plain',buffer:Buffer.from(timedGift)}}});
 if(!timedUpload.ok())throw new Error(await timedUpload.text());
 await teacher.fill('#questionSeconds','1');
 teacher.once('dialog',dialog=>dialog.accept());
 await teacher.click('#forceBtn');
 await page.getByText('Таймер 1',{exact:true}).waitFor();
 await page.locator('#questionTimer').waitFor();
 await teacher.close(); // Серверный таймер работает без открытой панели преподавателя.
 await page.getByText('Вы закончили',{exact:true}).waitFor({timeout:10000});
 const timedResults=await (await page.request.get(base+'/api/results')).json();
 const timedRow=timedResults.results[0];
 if(timedRow.correct!==0 || timedRow.incorrect!==2 || timedRow.unanswered!==2)throw new Error(JSON.stringify(timedResults));
 if(errors.length)throw new Error(errors.join('\n'));
 console.log('PASS: join, waiting, immediate advance after answer, reload/resume mid-test, single/multiple choice, true/false, short answer, results, finish, reset, mobile overflow, DOCX download, server timer without teacher, missing answers scored wrong, no JS errors');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
