(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let socket, reconnectTimer, ackTimer, retryDelay = 1000;
  let questionDeadline = null;
  let current = null, accepted = false, pending = false, finished = false, changingName = false;
  let name = sessionStorage.getItem('student_name') || '';
  let studentId = sessionStorage.getItem('student_id') || '';
  $('nameInput').value = name;

  function connection(state, label) {
    $('connection').dataset.state = state;
    $('connectionText').textContent = label;
    $('offlineBanner').hidden = state !== 'offline';
    updateSubmit();
  }
  function panels(active) {
    for (const id of ['joinPanel', 'statePanel', 'questionPanel']) $(id).hidden = id !== active;
  }
  function title(heading, description) {
    $('pageTitle').textContent = heading;
    $('pageDescription').textContent = description;
  }
  function showWaiting() {
    questionDeadline = null;
    current = null; pending = false; accepted = false; finished = false;
    clearTimeout(ackTimer);
    panels('statePanel');
    title('Вы подключены', 'Можно выдохнуть. Дальше всё появится автоматически.');
    $('stateTitle').textContent = 'Ждём начала тестирования';
    $('stateCopy').textContent = 'Вы в списке участников. Когда преподаватель начнёт тест, здесь появится первый вопрос.';
    $('stateIcon').textContent = '○';
    $('changeNameBtn').hidden = false;
    $('sideCard').dataset.active = 'false';
  }
  function showFinished() {
    questionDeadline = null;
    current = null; pending = false; accepted = false; finished = true;
    clearTimeout(ackTimer);
    panels('statePanel');
    title('Тест завершён', 'Спасибо за работу.');
    $('stateTitle').textContent = 'Вы закончили';
    $('stateCopy').textContent = 'Тестирование завершено. Результаты доступны преподавателю.';
    $('stateIcon').textContent = '✓';
    $('changeNameBtn').hidden = true;
    $('sideCard').dataset.active = 'false';
    $('stateTitle').focus();
  }
  function value() {
    if (!current) return null;
    if (current.type === 'short_answer') return $('shortAnswerInput').value.trim();
    const inputs = [...$('options').querySelectorAll('input:checked')];
    if (current.type === 'true_false') return inputs.length ? current.answers[Number(inputs[0].value)] : null;
    return inputs.map(input => Number(input.value));
  }
  function updateSubmit() {
    const answer = value();
    const empty = answer === null || answer === '' || (Array.isArray(answer) && !answer.length);
    $('sendBtn').disabled = !current || (questionDeadline !== null && performance.now() >= questionDeadline) || empty || accepted || pending || socket?.readyState !== WebSocket.OPEN;
  }
  function updateCountdown() {
    if (!current || questionDeadline === null) {
      $('questionTimer').textContent = '';
      return;
    }
    const seconds = Math.max(0, Math.ceil((questionDeadline - performance.now()) / 1000));
    $('questionTimer').textContent = `Осталось: ${seconds} с`;
    $('questionTimer').dataset.urgent = String(seconds <= 10);
    if (seconds === 0 && !accepted) {
      $('answers').disabled = true;
      $('answerStatus').textContent = pending ? 'Время вышло. Проверяем подтверждение ответа…' : 'Время вышло. Ответ не отправлен — вопрос засчитывается как неправильный.';
      $('sendBtn').disabled = true;
    }
  }
  setInterval(updateCountdown, 200);
  function showQuestion(data) {
    const same = current?.id === data.question.id;
    const draft = same ? value() : null;
    clearTimeout(ackTimer);
    current = data.question; pending = false; accepted = Boolean(data.answered); finished = false;
    questionDeadline = typeof data.remaining_seconds === 'number' ? performance.now() + data.remaining_seconds * 1000 : null;
    panels('questionPanel');
    title('Время подумать', 'Прочитайте вопрос и отправьте свой ответ.');
    $('sideCard').dataset.active = 'true';
    $('progressText').textContent = `Вопрос ${data.index + 1} из ${data.total}`;
    $('questionType').textContent = current.type === 'short_answer' ? 'Короткий ответ' : current.type === 'true_false' ? 'Верно / неверно' : 'Выбор ответа';
    const progress = Math.round((data.index + 1) / data.total * 100);
    $('progressBar').setAttribute('aria-valuenow', String(progress));
    $('progressBar').setAttribute('aria-valuetext', $('progressText').textContent);
    $('progressFill').style.width = `${progress}%`;
    $('questionTitle').textContent = current.title || 'Вопрос';
    $('questionText').textContent = current.text;
    $('questionText').hidden = current.title === current.text;
    $('options').replaceChildren();
    const saved = accepted ? data.answer : draft;
    if (current.type === 'short_answer') {
      $('answerHint').textContent = 'Введите свой ответ';
      const input = document.createElement('input');
      input.className = 'text-input'; input.id = 'shortAnswerInput'; input.type = 'text'; input.maxLength = 2000;
      input.setAttribute('aria-label', 'Ваш ответ'); input.autocomplete = 'off'; input.placeholder = 'Ваш ответ';
      input.value = typeof saved === 'string' ? saved : '';
      $('options').append(input);
    } else {
      $('answerHint').textContent = current.multiple ? 'Выберите один или несколько вариантов' : 'Выберите один вариант';
      current.answers.forEach((text, index) => {
        const label = document.createElement('label'); label.className = 'option';
        const input = document.createElement('input'); input.type = current.multiple ? 'checkbox' : 'radio';
        input.name = 'answer'; input.value = String(index);
        input.checked = current.type === 'true_false' ? saved === text : Array.isArray(saved) && saved.includes(index);
        const caption = document.createElement('span'); caption.className = 'option-text';
        caption.textContent = current.type === 'true_false' ? (text === 'TRUE' ? 'Верно' : 'Неверно') : text;
        label.append(input, caption); $('options').append(label);
      });
    }
    $('answers').disabled = accepted;
    $('answerStatus').dataset.tone = '';
    $('answerStatus').textContent = accepted ? 'Ответ сохранён. Ждём следующий вопрос.' : 'Выберите ответ, затем нажмите кнопку.';
    $('sendBtn').textContent = accepted ? 'Ответ сохранён' : 'Отправить ответ';
    updateCountdown();
    updateSubmit();
    if (!same) $('questionTitle').focus();
  }
  function connect() {
    clearTimeout(reconnectTimer);
    if (socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.readyState)) return;
    changingName = false;
    connection('connecting', 'Подключаемся…');
    const params = new URLSearchParams({name});
    if (studentId) params.set('student_id', studentId);
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/student?${params}`);
    const active = socket;
    socket.onmessage = event => {
      if (socket !== active) return;
      const data = JSON.parse(event.data);
      if (data.type === 'joined') {
        const lostSession = studentId && studentId !== data.student_id;
        studentId = data.student_id;
        sessionStorage.setItem('student_id', studentId);
        sessionStorage.setItem('student_name', name);
        $('studentName').textContent = name;
        retryDelay = 1000;
        connection('online', 'На связи');
        $('joinBtn').disabled = false; $('nameInput').disabled = false;
        if (!data.test_started && !data.test_finished) {
          showWaiting();
          if (lostSession) $('stateCopy').textContent = 'Сервер создал новое подключение. Предыдущая сессия недоступна — сообщите преподавателю.';
        }
      } else if (data.type === 'question') showQuestion(data);
      else if (data.type === 'finished') showFinished();
      else if (data.type === 'reset') showWaiting();
      else if (data.type === 'answer_saved' && data.question_id === current?.id) {
        clearTimeout(ackTimer); accepted = true; pending = false;
        $('answers').disabled = true;
        $('answerStatus').dataset.tone = '';
        $('answerStatus').textContent = 'Ответ сохранён. Ждём следующий вопрос.';
        $('sendBtn').textContent = 'Ответ сохранён'; updateSubmit();
      } else if (data.type === 'error') {
        if (data.question_id && data.question_id !== current?.id) return;
        clearTimeout(ackTimer); pending = false;
        $('answerStatus').textContent = data.message;
        $('answerStatus').dataset.tone = 'error';
        $('sendBtn').textContent = 'Отправить ответ';
        $('answers').disabled = accepted; updateSubmit();
      }
    };
    socket.onclose = event => {
      if (socket !== active || changingName) return;
      clearTimeout(ackTimer); pending = false;
      $('answers').disabled = accepted;
      $('sendBtn').textContent = accepted ? 'Ответ сохранён' : 'Отправить ответ';
      connection('offline', 'Нет связи');
      $('joinBtn').disabled = false; $('nameInput').disabled = false;
      if (event.code === 4001) {
        $('offlineBanner').querySelector('p').textContent = 'Тест открыт в другой вкладке. Закройте её перед повторным подключением.';
        return;
      }
      $('offlineBanner').querySelector('p').textContent = 'Связь с сервером потеряна. Подключаемся снова…';
      reconnectTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 8000);
    };
    socket.onerror = () => socket === active && active.close();
  }
  $('joinForm').addEventListener('submit', event => {
    event.preventDefault();
    name = $('nameInput').value.trim();
    if (!name) { $('nameInput').focus(); return; }
    $('joinBtn').disabled = true; $('nameInput').disabled = true;
    $('joinStatus').textContent = 'Подключаемся к тестированию…';
    connect();
  });
  $('options').addEventListener('input', updateSubmit);
  $('answerForm').addEventListener('submit', event => {
    event.preventDefault();
    updateSubmit();
    if ($('sendBtn').disabled) return;
    pending = true; $('answers').disabled = true;
    $('answerStatus').dataset.tone = '';
    $('answerStatus').textContent = 'Ожидаем подтверждение сервера…';
    $('sendBtn').textContent = 'Сохраняем…'; updateSubmit();
    socket.send(JSON.stringify({type: 'answer', question_id: current.id, value: value()}));
    ackTimer = setTimeout(() => {
      // Reconnect to ask the server whether it stored the answer before retrying.
      $('answerStatus').textContent = 'Проверяем, сохранился ли ответ…';
      socket.close();
    }, 10000);
  });
  $('retryBtn').addEventListener('click', connect);
  $('changeNameBtn').addEventListener('click', () => {
    changingName = true; clearTimeout(reconnectTimer); socket?.close(); socket = null;
    // Tell the next connection to update this participant's display name.
    panels('joinPanel'); connection('idle', 'Локальная сеть');
    title('Как вас представить?', 'Укажите фамилию и имя для списка участников.');
    $('joinStatus').textContent = ''; $('nameInput').focus();
  });
  setInterval(() => {
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: 'ping'}));
  }, 20000);
  if (name && studentId) {
    panels('statePanel'); $('changeNameBtn').hidden = true;
    $('stateTitle').textContent = 'Возвращаемся к тесту';
    $('stateCopy').textContent = 'Восстанавливаем подключение к серверу.';
    $('studentName').textContent = name;
    connect();
  }
})();
