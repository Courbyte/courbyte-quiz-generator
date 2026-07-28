const logList = document.getElementById('logList');
const toast = document.getElementById('toast');
let authToken = localStorage.getItem('authToken') || '';
let currentUsername = localStorage.getItem('username') || '';
let scheduleData = [];
let currentReviewData = null;
let modalMode = 'view';
let deleteTargetDate = null;

// --- Dynamic progress circles ---
function setCircleProgress(pathId, percent) {
    const circle = document.getElementById(pathId);
    if (circle) {
        const dashoffset = 100 - percent;
        circle.style.strokeDashoffset = dashoffset;
    }
}

async function updateProgressCircles() {
    try {
        const res = await fetch('/api/make-credits', {
            headers: { 'Authorization': `Bearer ${authToken}` }
        });
        const data = await res.json();
        if (data.success) {
            const makePercent = (data.remaining / data.limit) * 100;
            setCircleProgress('makeCreditsPath', makePercent);
            document.getElementById('makeCreditsValue').textContent = Math.round(data.remaining);
        } else {
            // Make.com API not configured or errored - fall back to full circle, no crash
            setCircleProgress('makeCreditsPath', 100);
            document.getElementById('makeCreditsValue').textContent = '—';
        }
    } catch (e) {
        setCircleProgress('makeCreditsPath', 100);
        document.getElementById('makeCreditsValue').textContent = '—';
    }

    const socialTotal = 6;
    const socialPercent = (6 / socialTotal) * 100;
    setCircleProgress('socialCountPath', socialPercent);
    document.getElementById('socialCountValue').textContent = 6;
}

function showToast(message, type = 'success') {
    toast.textContent = message;
    toast.className = 'toast ' + type;
    toast.style.display = 'block';
    setTimeout(() => { toast.style.display = 'none'; }, 3000);
}

function showLogin() {
    document.getElementById('loginOverlay').classList.remove('hidden');
    document.getElementById('dashboardContent').classList.add('hidden');
}

function showDashboard() {
    document.getElementById('loginOverlay').classList.add('hidden');
    document.getElementById('dashboardContent').classList.remove('hidden');
    closeQuestionModal();
    closeDeleteModal();
}

function togglePasswordVisibility() {
    const input = document.getElementById('loginPassword');
    const icon = document.getElementById('passwordToggleIcon');
    if (input.type === 'password') {
        input.type = 'text';
        icon.classList.remove('fa-lock');
        icon.classList.add('fa-lock-open');
    } else {
        input.type = 'password';
        icon.classList.remove('fa-lock-open');
        icon.classList.add('fa-lock');
    }
}

async function login() {
    const username = document.getElementById('loginUsername').value.trim();
    const password = document.getElementById('loginPassword').value;
    const passwordRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$/;
    if (!username || !password) {
        document.getElementById('loginError').innerText = 'Enter username and password.';
        return;
    }
    if (!passwordRegex.test(password)) {
        document.getElementById('loginError').innerText = 'Password needs an uppercase letter, a lowercase letter, a number, and a special symbol (min 8 characters).';
        return;
    }
    document.getElementById('loginError').innerText = '';
    const loginBtn = document.getElementById('loginSubmitBtn');
    const originalBtnHtml = loginBtn.innerHTML;
    loginBtn.disabled = true;
    loginBtn.innerHTML = '<span class="spinner"></span> Logging in...';
    try {
        const res = await fetch('/api/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();
        if (data.success) {
            authToken = data.token;
            currentUsername = data.username;
            localStorage.setItem('authToken', authToken);
            localStorage.setItem('username', currentUsername);
            showDashboard();
            await loadSchedule();
            updateProgressCircles();
            showToast('Login successful!', 'success');
        } else {
            document.getElementById('loginError').innerText = data.message || 'Login failed';
        }
    } catch (e) {
        document.getElementById('loginError').innerText = 'Network error. Please try again.';
    } finally {
        loginBtn.disabled = false;
        loginBtn.innerHTML = originalBtnHtml;
    }
}

function logout() {
    localStorage.removeItem('authToken');
    localStorage.removeItem('username');
    authToken = '';
    currentUsername = '';
    showLogin();
    showToast('Logged out successfully', 'success');
}

async function loadSchedule() {
    const res = await fetch('/api/schedule', {
        headers: { 'Authorization': `Bearer ${authToken}` }
    });
    scheduleData = await res.json();
    renderTable();
}

function isPastDateTime(dateStr, timeStr) {
    const now = new Date();
    const slotDate = new Date(dateStr + 'T' + timeStr);
    return slotDate < now;
}

function renderTable() {
    const tbody = document.getElementById('scheduleTbody');
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

    scheduleData.forEach((day, dayIndex) => {
        const tr = document.createElement('tr');

        const tdCheck = document.createElement('td');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'select-checkbox';
        cb.dataset.dayIndex = dayIndex;
        tdCheck.appendChild(cb);
        tr.appendChild(tdCheck);

        const tdDate = document.createElement('td');
        tdDate.textContent = day.date;
        tr.appendChild(tdDate);

        const slot9 = day.slots[0];
        const td9 = document.createElement('td');
        const slot9Text = document.createElement('span');
        slot9Text.innerHTML = `${slot9.topic || '—'} <span class="slot-q">(${slot9.questions} q)</span>`;
        td9.appendChild(slot9Text);
        const qBtn9 = document.createElement('button');
        qBtn9.className = 'slot-action-btn';
        qBtn9.innerHTML = '<i class="fa-solid fa-circle-question"></i>';
        qBtn9.title = 'Preview questions';
        qBtn9.onclick = (e) => {
            e.stopPropagation();
            previewQuestions(day.date, '09:00', slot9.topic, slot9.questions);
        };
        td9.appendChild(qBtn9);
        const vBtn9 = document.createElement('button');
        vBtn9.className = 'slot-action-btn';
        vBtn9.innerHTML = '<i class="fa-solid fa-video"></i>';
        vBtn9.title = 'Generate video now';
        vBtn9.onclick = (e) => {
            e.stopPropagation();
            generateSlotVideo(day.date, '09:00');
        };
        td9.appendChild(vBtn9);
        tr.appendChild(td9);

        const td9Posted = document.createElement('td');
        const posted9Icon = document.createElement('i');
        posted9Icon.className = 'fa-solid ' + (slot9.posted ? 'fa-circle-check posted-icon' : 'fa-circle');
        posted9Icon.style.cursor = 'pointer';
        posted9Icon.onclick = () => togglePosted(dayIndex, 0, posted9Icon);
        td9Posted.appendChild(posted9Icon);
        const autoPost9Icon = document.createElement('i');
        autoPost9Icon.className = 'fa-solid fa-bullhorn autopost-icon' + (slot9.auto_post !== false ? ' active' : '');
        autoPost9Icon.title = slot9.auto_post !== false ? 'Auto-post: ON (click to disable)' : 'Auto-post: OFF (click to enable)';
        autoPost9Icon.onclick = () => toggleAutoPost(dayIndex, 0, autoPost9Icon);
        td9Posted.appendChild(autoPost9Icon);
        tr.appendChild(td9Posted);

        const slot18 = day.slots[1];
        const td18 = document.createElement('td');
        const slot18Text = document.createElement('span');
        slot18Text.innerHTML = `${slot18.topic || '—'} <span class="slot-q">(${slot18.questions} q)</span>`;
        td18.appendChild(slot18Text);
        const qBtn18 = document.createElement('button');
        qBtn18.className = 'slot-action-btn';
        qBtn18.innerHTML = '<i class="fa-solid fa-circle-question"></i>';
        qBtn18.title = 'Preview questions';
        qBtn18.onclick = (e) => {
            e.stopPropagation();
            previewQuestions(day.date, '18:00', slot18.topic, slot18.questions);
        };
        td18.appendChild(qBtn18);
        const vBtn18 = document.createElement('button');
        vBtn18.className = 'slot-action-btn';
        vBtn18.innerHTML = '<i class="fa-solid fa-video"></i>';
        vBtn18.title = 'Generate video now';
        vBtn18.onclick = (e) => {
            e.stopPropagation();
            generateSlotVideo(day.date, '18:00');
        };
        td18.appendChild(vBtn18);
        tr.appendChild(td18);

        const td18Posted = document.createElement('td');
        const posted18Icon = document.createElement('i');
        posted18Icon.className = 'fa-solid ' + (slot18.posted ? 'fa-circle-check posted-icon' : 'fa-circle');
        posted18Icon.style.cursor = 'pointer';
        posted18Icon.onclick = () => togglePosted(dayIndex, 1, posted18Icon);
        td18Posted.appendChild(posted18Icon);
        const autoPost18Icon = document.createElement('i');
        autoPost18Icon.className = 'fa-solid fa-bullhorn autopost-icon' + (slot18.auto_post !== false ? ' active' : '');
        autoPost18Icon.title = slot18.auto_post !== false ? 'Auto-post: ON (click to disable)' : 'Auto-post: OFF (click to enable)';
        autoPost18Icon.onclick = () => toggleAutoPost(dayIndex, 1, autoPost18Icon);
        td18Posted.appendChild(autoPost18Icon);
        tr.appendChild(td18Posted);

        const tdActions = document.createElement('td');
        const editBtn = document.createElement('button');
        editBtn.className = 'icon-btn';
        editBtn.innerHTML = '<i class="fa-solid fa-pencil"></i>';
        editBtn.title = 'Edit';
        editBtn.onclick = () => startEditRow(tr, dayIndex);
        tdActions.appendChild(editBtn);

        const genBtn = document.createElement('button');
        genBtn.className = 'icon-btn';
        genBtn.innerHTML = '<i class="fa-solid fa-play"></i>';
        genBtn.title = 'Generate & Review Questions';
        genBtn.onclick = () => generateAndReview(dayIndex);
        tdActions.appendChild(genBtn);

        const delBtn = document.createElement('button');
        delBtn.className = 'icon-btn';
        delBtn.innerHTML = '<i class="fa-solid fa-trash"></i>';
        delBtn.title = 'Delete day';
        delBtn.onclick = () => openDeleteModal(day.date);
        tdActions.appendChild(delBtn);

        tr.appendChild(tdActions);
        tbody.appendChild(tr);
    });
}

function togglePosted(dayIdx, slotIdx, iconEl) {
    const slot = scheduleData[dayIdx].slots[slotIdx];
    slot.posted = !slot.posted;
    iconEl.className = 'fa-solid ' + (slot.posted ? 'fa-circle-check posted-icon' : 'fa-circle');
}

function toggleAutoPost(dayIdx, slotIdx, iconEl) {
    const slot = scheduleData[dayIdx].slots[slotIdx];
    const currentlyOn = slot.auto_post !== false;
    slot.auto_post = !currentlyOn;
    if (slot.auto_post) {
        iconEl.classList.add('active');
        iconEl.title = 'Auto-post: ON (click to disable)';
    } else {
        iconEl.classList.remove('active');
        iconEl.title = 'Auto-post: OFF (click to enable)';
    }
}

function startEditRow(tr, dayIndex) {
    const day = scheduleData[dayIndex];
    const cells = tr.querySelectorAll('td');
    const td9 = cells[2];
    const td18 = cells[4];

    if (!isPastDateTime(day.date, '09:00')) {
        td9.innerHTML = `<input class="slot-edit-input" id="edit-topic-${dayIndex}-0" value="${day.slots[0].topic}" placeholder="Topic">
                         <input class="slot-edit-input" id="edit-q-${dayIndex}-0" type="number" value="${day.slots[0].questions}" style="width:60px;" min="1">`;
    }
    if (!isPastDateTime(day.date, '18:00')) {
        td18.innerHTML = `<input class="slot-edit-input" id="edit-topic-${dayIndex}-1" value="${day.slots[1].topic}" placeholder="Topic">
                         <input class="slot-edit-input" id="edit-q-${dayIndex}-1" type="number" value="${day.slots[1].questions}" style="width:60px;" min="1">`;
    }

    const actionsCell = cells[6];
    const editBtn = actionsCell.querySelector('.icon-btn');
    editBtn.innerHTML = '<i class="fa-solid fa-save"></i>';
    editBtn.onclick = () => saveEditRow(tr, dayIndex);
}

function saveEditRow(tr, dayIndex) {
    const day = scheduleData[dayIndex];
    const topic9Input = document.getElementById(`edit-topic-${dayIndex}-0`);
    const q9Input = document.getElementById(`edit-q-${dayIndex}-0`);
    if (topic9Input) day.slots[0].topic = topic9Input.value;
    if (q9Input) day.slots[0].questions = parseInt(q9Input.value) || 5;

    const topic18Input = document.getElementById(`edit-topic-${dayIndex}-1`);
    const q18Input = document.getElementById(`edit-q-${dayIndex}-1`);
    if (topic18Input) day.slots[1].topic = topic18Input.value;
    if (q18Input) day.slots[1].questions = parseInt(q18Input.value) || 5;

    renderTable();
}

async function saveSchedule() {
    await fetch('/api/schedule', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(scheduleData)
    });
    showToast('Schedule saved!');
}

function addNewDay() {
    let lastDate = new Date();
    if (scheduleData.length > 0) {
        const dates = scheduleData.map(d => new Date(d.date));
        lastDate = new Date(Math.max(...dates));
    }
    const nextDate = new Date(lastDate);
    nextDate.setDate(nextDate.getDate() + 1);
    const dateStr = nextDate.toISOString().split('T')[0];
    scheduleData.push({
        date: dateStr,
        slots: [
            { time: "09:00", topic: "", questions: 5, posted: false, auto_post: true },
            { time: "18:00", topic: "", questions: 5, posted: false, auto_post: true }
        ]
    });
    renderTable();
}

async function suggestTopics() {
    const selected = [];
    document.querySelectorAll('.select-checkbox:checked').forEach(cb => {
        const idx = parseInt(cb.dataset.dayIndex);
        selected.push(scheduleData[idx].date);
    });
    if (selected.length === 0) {
        showToast('Select at least one day.', 'error');
        return;
    }
    const res = await fetch('/api/suggest-topics', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ dates: selected })
    });
    const data = await res.json();
    if (data.success) {
        scheduleData = data.schedule;
        renderTable();
        showToast('Topics suggested and applied!');
    } else {
        showToast('Error: ' + (data.error || 'Unknown'), 'error');
    }
}

function toggleAllDays() {
    const checkboxes = document.querySelectorAll('.select-checkbox');
    const allChecked = Array.from(checkboxes).every(cb => cb.checked);
    checkboxes.forEach(cb => cb.checked = !allChecked);
}

async function previewQuestions(dateStr, timeStr, topic, numQuestions) {
    if (!topic) {
        showToast('No topic entered for this slot.', 'error');
        return;
    }
    try {
        const res = await fetch('/api/generate-questions', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ topic, num: numQuestions })
        });
        const data = await res.json();
        if (data.success) {
            currentReviewData = [{
                slotTime: timeStr,
                topic: topic,
                questions: data.questions
            }];
            modalMode = 'view';
            openQuestionModal();
        } else {
            showToast('Error: ' + data.error, 'error');
        }
    } catch(e) {
        showToast('Network error', 'error');
    }
}

async function generateAndReview(dayIdx) {
    const day = scheduleData[dayIdx];
    const slotsWithTopics = day.slots.filter(s => s.topic.trim());
    if (slotsWithTopics.length === 0) {
        showToast('No topics in this day.', 'error');
        return;
    }

    const reviewGroups = [];
    for (const slot of slotsWithTopics) {
        try {
            const res = await fetch('/api/generate-questions', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
                body: JSON.stringify({ topic: slot.topic, num: slot.questions })
            });
            const data = await res.json();
            if (data.success) {
                reviewGroups.push({
                    slotTime: slot.time,
                    topic: slot.topic,
                    questions: data.questions,
                    autoPost: slot.auto_post !== false
                });
            } else {
                showToast(`Error generating for ${slot.topic}: ${data.error}`, 'error');
            }
        } catch(e) {
            showToast('Network error', 'error');
        }
    }

    if (reviewGroups.length > 0) {
        currentReviewData = reviewGroups;
        modalMode = 'create';
        openQuestionModal();
    }
}

function openQuestionModal() {
    const modal = document.getElementById('questionModal');
    const container = document.getElementById('modalQuestionsContainer');
    const titleEl = document.getElementById('modalTitle');
    const actionsDiv = document.getElementById('modalActions');

    container.innerHTML = '';

    if (modalMode === 'create') {
        titleEl.textContent = 'Review/Edit Questions';
        actionsDiv.innerHTML = '<button onclick="createVideosFromModal()"><i class="fa-solid fa-video"></i> Create Videos</button>';
    } else {
        titleEl.textContent = 'Question Preview';
        actionsDiv.innerHTML = '<button class="btn-secondary" onclick="closeQuestionModal()">Close</button>';
    }

    currentReviewData.forEach((group, gIdx) => {
        const groupDiv = document.createElement('div');
        groupDiv.innerHTML = `<h4 style="margin:1rem 0 0.5rem; color:var(--accent);">${group.slotTime} – ${group.topic}</h4>`;
        container.appendChild(groupDiv);

        group.questions.forEach((q, qIdx) => {
            const qBlock = document.createElement('div');
            qBlock.className = 'question-block';
            if (modalMode === 'create') {
                qBlock.innerHTML = `
                    <label>Question ${qIdx+1} text</label>
                    <input type="text" class="qtext" value="${q.text}" data-gidx="${gIdx}" data-qidx="${qIdx}">
                    <div class="option-row" style="margin-top:0.5rem;">
                        <input type="text" class="optA" value="${q.options[0].text}" data-gidx="${gIdx}" data-qidx="${qIdx}" data-optidx="0">
                        <input type="radio" name="correct-${gIdx}-${qIdx}" class="correct" value="0" ${q.options[0].isCorrect ? 'checked' : ''}>
                    </div>
                    <div class="option-row">
                        <input type="text" class="optB" value="${q.options[1].text}" data-gidx="${gIdx}" data-qidx="${qIdx}" data-optidx="1">
                        <input type="radio" name="correct-${gIdx}-${qIdx}" class="correct" value="1" ${q.options[1].isCorrect ? 'checked' : ''}>
                    </div>
                    <div class="option-row">
                        <input type="text" class="optC" value="${q.options[2].text}" data-gidx="${gIdx}" data-qidx="${qIdx}" data-optidx="2">
                        <input type="radio" name="correct-${gIdx}-${qIdx}" class="correct" value="2" ${q.options[2].isCorrect ? 'checked' : ''}>
                    </div>
                    <div class="option-row">
                        <input type="text" class="optD" value="${q.options[3].text}" data-gidx="${gIdx}" data-qidx="${qIdx}" data-optidx="3">
                        <input type="radio" name="correct-${gIdx}-${qIdx}" class="correct" value="3" ${q.options[3].isCorrect ? 'checked' : ''}>
                    </div>
                `;
            } else {
                qBlock.innerHTML = `
                    <p><strong>Q${qIdx+1}:</strong> ${q.text}</p>
                    ${q.options.map((opt, oi) => {
                        const prefix = String.fromCharCode(97+oi) + ') ';
                        const isCorrect = opt.isCorrect;
                        return `<div style="margin-left:1rem; color:${isCorrect ? 'var(--success)' : 'var(--text)'};">
                            ${prefix} ${opt.text} ${isCorrect ? '✓' : ''}
                        </div>`;
                    }).join('')}
                `;
            }
            container.appendChild(qBlock);
        });
    });

    modal.style.display = 'flex';
}

function closeQuestionModal() {
    document.getElementById('questionModal').style.display = 'none';
    currentReviewData = null;
}

async function createVideosFromModal() {
    if (!currentReviewData) return;

    for (const group of currentReviewData) {
        if (modalMode === 'create') {
            const gIdx = currentReviewData.indexOf(group);
            group.questions.forEach((q, qIdx) => {
                const textEl = document.querySelector(`.qtext[data-gidx="${gIdx}"][data-qidx="${qIdx}"]`);
                if (textEl) q.text = textEl.value;

                for (let optIdx = 0; optIdx < 4; optIdx++) {
                    const optEl = document.querySelector(`.opt${String.fromCharCode(65+optIdx)}[data-gidx="${gIdx}"][data-qidx="${qIdx}"]`);
                    if (optEl) q.options[optIdx].text = optEl.value;
                }

                const correctRadio = document.querySelector(`input[name="correct-${gIdx}-${qIdx}"]:checked`);
                if (correctRadio) {
                    const correctVal = parseInt(correctRadio.value);
                    q.options.forEach((opt, idx) => { opt.isCorrect = idx === correctVal; });
                }
            });
        }

        try {
            const res = await fetch('/api/create-video', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
                body: JSON.stringify({ questions: group.questions, topic: group.topic, auto_post: group.autoPost !== false })
            });
            const data = await res.json();
            if (data.success) {
                addLog(group.topic, 'success', data.files[0], data.supabase_url);
            } else {
                addLog(group.topic, 'error', null, null);
            }
        } catch(e) {
            addLog(group.topic, 'error', null, null);
        }
    }
    closeQuestionModal();
    showToast('Video(s) created!');
}

async function generateSlotVideo(dateStr, timeStr) {
    const day = scheduleData.find(d => d.date === dateStr);
    if (!day) return;
    const slot = day.slots.find(s => s.time === timeStr);
    if (!slot || !slot.topic.trim()) {
        showToast('No topic entered for this slot.', 'error');
        return;
    }
    try {
        const res = await fetch('/api/generate-slot', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: dateStr, time: timeStr })
        });
        const data = await res.json();
        if (data.success) {
            addLog(data.topic, 'success', data.files[0], data.supabase_url);
            showToast('Video generated successfully!');
        } else {
            showToast('Error: ' + data.error, 'error');
        }
    } catch(e) {
        showToast('Network error', 'error');
    }
}

function openDeleteModal(dateStr) {
    deleteTargetDate = dateStr;
    const inputs = document.querySelectorAll('#deletePinContainer input');
    inputs.forEach(inp => inp.value = '');
    document.getElementById('deleteError').innerText = '';
    document.getElementById('deleteModal').style.display = 'flex';
}

function closeDeleteModal() {
    document.getElementById('deleteModal').style.display = 'none';
    deleteTargetDate = null;
}

async function confirmDelete() {
    const pinInputs = document.querySelectorAll('#deletePinContainer input');
    let pin = '';
    pinInputs.forEach(inp => pin += inp.value);
    if (pin.length !== 6) {
        document.getElementById('deleteError').innerText = 'Enter a 6-digit PIN.';
        return;
    }
    if (!currentUsername) {
        document.getElementById('deleteError').innerText = 'You must be logged in.';
        return;
    }
    try {
        const res = await fetch('/api/delete-day', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: currentUsername, pin, date: deleteTargetDate })
        });
        const data = await res.json();
        if (data.success) {
            scheduleData = scheduleData.filter(d => d.date !== deleteTargetDate);
            renderTable();
            closeDeleteModal();
            showToast('Day deleted successfully.');
        } else {
            document.getElementById('deleteError').innerText = data.error || 'Deletion failed';
        }
    } catch(e) {
        document.getElementById('deleteError').innerText = 'Network error';
    }
}

function addLog(topic, status, localFile, supabaseUrl) {
    const li = document.createElement('li');
    li.className = 'log-item';

    const timeSpan = document.createElement('span');
    timeSpan.className = 'time';
    const now = new Date();
    timeSpan.textContent = now.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    li.appendChild(timeSpan);

    const statusIcon = document.createElement('i');
    statusIcon.className = 'fa-solid ' + (status === 'success' ? 'fa-circle-check success' : 'fa-circle-exclamation error');
    statusIcon.style.color = status === 'success' ? 'var(--success)' : 'var(--danger)';
    li.appendChild(statusIcon);

    const topicSpan = document.createElement('span');
    topicSpan.className = 'topic';
    topicSpan.textContent = topic;
    li.appendChild(topicSpan);

    const statusText = document.createElement('span');
    statusText.className = 'status ' + status;
    statusText.textContent = status === 'success' ? 'Done' : 'Error';
    li.appendChild(statusText);

    if (localFile) {
        const localBadge = document.createElement('span');
        localBadge.className = 'badge local';
        localBadge.innerHTML = '<i class="fa-solid fa-folder"></i> Local';
        li.appendChild(localBadge);
    }
    if (supabaseUrl) {
        const cloudBadge = document.createElement('span');
        cloudBadge.className = 'badge cloud';
        cloudBadge.innerHTML = '<i class="fa-solid fa-cloud"></i> Cloud';
        li.appendChild(cloudBadge);

        const socialSpan = document.createElement('span');
        socialSpan.className = 'social-icons';
        socialSpan.innerHTML = '<i class="fa-brands fa-youtube" title="YouTube"></i><i class="fa-brands fa-facebook" title="Facebook"></i><i class="fa-brands fa-instagram" title="Instagram"></i><i class="fa-brands fa-tiktok" title="TikTok"></i><i class="fa-brands fa-linkedin" title="LinkedIn"></i><i class="fa-brands fa-pinterest" title="Pinterest"></i>';
        li.appendChild(socialSpan);
    }

    logList.prepend(li);
    while (logList.children.length > 21) logList.lastChild.remove();
}

function clearLogs() {
    while (logList.firstChild) logList.removeChild(logList.firstChild);
}

if (authToken) {
    showDashboard();
    loadSchedule().catch(() => { logout(); });
    updateProgressCircles();
} else {
    showLogin();
}
