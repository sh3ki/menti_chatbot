(function () {
  const style = document.createElement('style');
  style.textContent = `
    .grid { align-items: start; }
    #providers { max-height: 430px; overflow-y: auto; padding-right: 6px; }
    #providers::-webkit-scrollbar { width: 8px; }
    #providers::-webkit-scrollbar-thumb { background: #cbd8cc; border-radius: 99px; }
    .provider { transition: border-color .15s, background .15s, box-shadow .15s; }
    .provider:hover { border-color: #4fa858; background: #f7fbf7; box-shadow: 0 2px 8px rgba(44,94,49,.08); }
    .provider-role-filter { width: 100%; max-width: 260px; padding: 11px 38px 11px 13px; border: 1px solid #cbd8cc; border-radius: 10px; background: #fff; color: #17221a; font: 500 14px Inter, Arial, sans-serif; cursor: pointer; appearance: none; background-image: linear-gradient(45deg, transparent 50%, #2c5e31 50%), linear-gradient(135deg, #2c5e31 50%, transparent 50%); background-position: calc(100% - 18px) 16px, calc(100% - 12px) 16px; background-size: 6px 6px, 6px 6px; background-repeat: no-repeat; }
    .provider-role-filter:focus { outline: 2px solid #b9dcbc; border-color: #2c5e31; }
    .booking-modal { display:none; position:fixed; inset:0; z-index:1200; background:rgba(15,25,17,.54); align-items:center; justify-content:center; padding:20px; }
    .booking-modal.open { display:flex; }
    .booking-modal-card { width:min(920px, 96vw); max-height:92vh; overflow-y:auto; background:#fff; border-radius:18px; padding:24px; box-shadow:0 18px 55px rgba(0,0,0,.24); position:relative; }
    .booking-modal-header { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:12px; }
    .booking-modal-header h2 { margin:0; font-size:24px; }
    .booking-modal-close { border:0; background:#f0f4f0; color:#284b2d; width:34px; height:34px; border-radius:50%; font-size:23px; cursor:pointer; line-height:1; }
    .selected-provider { display:flex; align-items:center; gap:12px; margin-top:10px; }
    .selected-provider-avatar { width:44px; height:44px; border-radius:50%; flex:0 0 44px; overflow:hidden; display:grid; place-items:center; background:#e8f5e9; color:#2c5e31; font-weight:800; font-size:18px; }
    .selected-provider-avatar img { width:100%; height:100%; object-fit:contain; }
    .selected-provider-name { font-size:16px; font-weight:800; color:#17221a; }
    .selected-provider-meta { margin-top:3px; color:#718076; font-size:13px; }
    .selected-provider-card.provider { margin:10px 0 0; cursor:default; box-shadow:none; }
    .selected-provider-card.provider:hover { border-color:#e1eae2; background:#fff; box-shadow:none; }
    #providerCalendar { margin:14px 0 18px; border:1px solid #dfe8e0; border-radius:12px; padding:14px; }
    .calendar-toolbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
    .calendar-toolbar button { border:1px solid #d4e0d5; background:#fff; border-radius:7px; padding:5px 10px; cursor:pointer; color:#2c5e31; }
    .calendar-month { font-weight:800; color:#2c5e31; }
    .calendar-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:4px; }
    .calendar-weekday { font-size:11px; color:#718076; text-align:center; font-weight:700; padding:4px 0; }
    .calendar-day { min-height:58px; border:1px solid #edf2ed; border-radius:7px; padding:5px; font-size:12px; color:#334437; background:#fff; }
    .calendar-day:not(.muted) { cursor:pointer; }
    .calendar-day:not(.muted):hover, .calendar-day.selected { border-color:#4fa858; background:#f4fbf4; }
    .calendar-day.muted { background:#fafcfa; color:#a5b0a7; }
    .calendar-day-number { font-weight:700; }
    .calendar-events { position:relative; margin-top:7px; }
    .calendar-event { height:18px; line-height:18px; border-radius:4px; background:#d94b4b; color:#fff; padding:0 5px; font-size:10px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .calendar-event-count { color:#b22f2f; font-size:10px; font-weight:700; margin-top:2px; }
    .calendar-tooltip { display:none; position:absolute; left:0; top:calc(100% + 5px); z-index:10; min-width:170px; max-width:240px; padding:8px 10px; border-radius:8px; background:#242424; color:#fff; font-size:11px; line-height:1.45; box-shadow:0 5px 16px rgba(0,0,0,.2); white-space:normal; }
    .calendar-day:hover .calendar-tooltip, .calendar-events:hover .calendar-tooltip { display:block; }
    .booking-modal #booking { display:block !important; }
    body.dark-mode { background:#1a1a1a; color:#fff; }
    body.dark-mode .header { background:#242424; border-bottom-color:#3a3a3a; }
    body.dark-mode .header h1, body.dark-mode .title { color:#4fa858; }
    body.dark-mode .main-container { background:#1a1a1a; }
    body.dark-mode .sidebar { background:#242424; border-right-color:#3a3a3a; }
    body.dark-mode .sidebar-header { background:#242424; border-bottom-color:#3a3a3a; }
    body.dark-mode .sidebar-content { background:#242424; scrollbar-color:#4a4a4a #242424; }
    body.dark-mode .sidebar-content::-webkit-scrollbar { width:6px; }
    body.dark-mode .sidebar-content::-webkit-scrollbar-track { background:#242424; }
    body.dark-mode .sidebar-content::-webkit-scrollbar-thumb { background:#4a4a4a; border-radius:3px; }
    body.dark-mode .sidebar-content::-webkit-scrollbar-thumb:hover { background:#5a5a5a; }
    body.dark-mode .toggle-archived-btn { background:#2c2c2c; color:#fff; border-color:#3a3a3a; }
    body.dark-mode .toggle-archived-btn:hover { background:#3a3a3a; border-color:#4fa858; }
    body.dark-mode .toggle-archived-btn.active { background:#1f3a21; color:#a0d8a0; border-color:#4fa858; }
    body.dark-mode .sidebar-section-title { color:#fff; }
    body.dark-mode .conv-item { color:#fff; }
    body.dark-mode .conv-item:hover { background:#3a3a3a; }
    body.dark-mode .empty-state { color:#fff; }
    .main-container > .main { min-height:0; overflow-y:auto; scrollbar-color:#4a4a4a transparent; }
    .main-container > .main::-webkit-scrollbar { width:7px; }
    .main-container > .main::-webkit-scrollbar-thumb { background:#b8cfbb; border-radius:10px; }
    body.dark-mode .main-container > .main::-webkit-scrollbar-thumb { background:#4a4a4a; }
    body.dark-mode .main, body.dark-mode .card, body.dark-mode .provider-role-filter, body.dark-mode .booking-modal-card, body.dark-mode .calendar-day, body.dark-mode .calendar-toolbar button { background:#2c2c2c; color:#fff; border-color:#465548; }
    body.dark-mode .main { background:#1a1a1a; }
    body.dark-mode .provider { border-color:#465548; background:#242424; color:#fff; }
    body.dark-mode .provider:hover { background:#1f3a21; border-color:#4fa858; }
    body.dark-mode .muted, body.dark-mode .field label { color:#b8c5ba; }
    body.dark-mode .field input, body.dark-mode .field select, body.dark-mode .field textarea { background:#242424; color:#fff; border-color:#465548; }
    body.dark-mode #providerCalendar { border-color:#465548; }
    body.dark-mode .calendar-day.muted { background:#242424; color:#6f7c71; }
    body.dark-mode .booking-modal-close { background:#3a3a3a; color:#fff; }
    body.dark-mode .selected-provider-name { color:#fff; }
    @media (max-width: 800px) { .booking-modal-card { padding:18px; } .calendar-day { min-height:46px; } }
  `;
  document.head.appendChild(style);

  function makeModal() {
    const modal = document.createElement('div');
    modal.className = 'booking-modal';
    modal.id = 'bookingModal';
    modal.innerHTML = '<div class="booking-modal-card"><div class="booking-modal-header"><div><h2>Request a counseling session</h2><p class="muted" id="selectedProviderLabel">Choose a provider and time that works for you.</p></div><button class="booking-modal-close" type="button" aria-label="Close">×</button></div><div id="providerCalendar"></div><div id="bookingModalBody"></div></div>';
    document.body.appendChild(modal);
    const close = () => modal.classList.remove('open');
    // Allow the appointment form to close the shared booking modal after a successful request.
    window.closeCounselingBookingModal = close;
    modal.querySelector('.booking-modal-close').onclick = close;
    modal.addEventListener('click', event => { if (event.target === modal) close(); });
    return modal;
  }

  function renderCalendar(container, schedule, monthDate) {
    const year = monthDate.getFullYear();
    const month = monthDate.getMonth();
    const first = new Date(year, month, 1);
    const days = new Date(year, month + 1, 0).getDate();
    const offset = first.getDay();
    const events = (schedule && schedule.events) || [];
    const eventsByDate = {};
    events.forEach(event => {
      const value = event.start || event.startTime || event.date;
      if (!value) return;
      const date = new Date(value);
      const key = `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
      (eventsByDate[key] ||= []).push(event);
    });
    const monthLabel = monthDate.toLocaleString('en-US', { month: 'long', year: 'numeric' });
    let html = `<div class="calendar-toolbar"><button type="button" data-calendar-prev>‹</button><span class="calendar-month">${monthLabel}</span><button type="button" data-calendar-next>›</button></div><div class="calendar-grid">${['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].map(day => `<div class="calendar-weekday">${day}</div>`).join('')}`;
    for (let i = 0; i < offset; i++) html += '<div class="calendar-day muted"></div>';
    for (let day = 1; day <= days; day++) {
      const dayEvents = eventsByDate[`${year}-${month}-${day}`] || [];
      const unavailableTimes = dayEvents.map(event => {
        const start = event.start || event.startTime || event.date;
        const end = event.end || event.endTime;
        if (!start) return 'Unavailable time';
        const startText = new Date(start).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
        const endText = end ? new Date(end).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '';
        return endText ? `${startText} – ${endText}` : startText;
      });
      const eventMarkup = dayEvents.length ? `<div class="calendar-events"><div class="calendar-event" aria-label="${dayEvents.length} unavailable event${dayEvents.length === 1 ? '' : 's'}">Event</div>${dayEvents.length > 1 ? `<div class="calendar-event-count">+${dayEvents.length - 1} event${dayEvents.length === 2 ? '' : 's'}</div>` : ''}<div class="calendar-tooltip">${unavailableTimes.map(time => `<div>${time}</div>`).join('')}</div></div>` : '';
      html += `<div class="calendar-day" data-calendar-date="${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}"><div class="calendar-day-number">${day}</div>${eventMarkup}</div>`;
    }
    html += '</div>';
    container.innerHTML = html;
    container.querySelector('[data-calendar-prev]').onclick = () => renderCalendar(container, schedule, new Date(year, month - 1, 1));
    container.querySelector('[data-calendar-next]').onclick = () => renderCalendar(container, schedule, new Date(year, month + 1, 1));
    container.querySelectorAll('[data-calendar-date]').forEach(dayCell => dayCell.onclick = event => {
      if (event.target.closest('.calendar-events')) return;
      const dateInput = document.getElementById('date');
      if (dateInput) { dateInput.value = dayCell.dataset.calendarDate; dateInput.dispatchEvent(new Event('change', { bubbles: true })); }
      container.querySelectorAll('.calendar-day.selected').forEach(cell => cell.classList.remove('selected'));
      dayCell.classList.add('selected');
    });
  }

  async function init() {
    const providers = document.getElementById('providers');
    if (!providers) return;
    const modal = makeModal();
    const modalBody = document.getElementById('bookingModalBody');
    const calendar = document.getElementById('providerCalendar');
    const styleRoleFilter = () => {
      const filter = Array.from(document.querySelectorAll('select')).find(select => select.options?.[0]?.textContent === 'All professionals');
      if (filter) filter.classList.add('provider-role-filter');
    };
    const openForCard = card => {
      const providerId = card.dataset.id;
      let tries = 0;
      const waitForBooking = setInterval(async () => {
        const booking = document.getElementById('booking');
        if (++tries > 50) clearInterval(waitForBooking);
        if (!booking || booking.style.display === 'none') return;
        clearInterval(waitForBooking);
        modalBody.appendChild(booking);
        const providerCard = card.cloneNode(true);
        providerCard.classList.add('selected-provider-card');
        providerCard.removeAttribute('data-modal-bound');
        document.getElementById('selectedProviderLabel').replaceChildren(providerCard);
        modal.classList.add('open');
        try {
          const schedule = await fetch(`/api/counseling/providers/${providerId}/schedule`).then(response => response.json());
          renderCalendar(calendar, schedule, new Date());
        } catch (_) { calendar.innerHTML = '<p class="muted">Provider calendar is unavailable right now.</p>'; }
      }, 100);
    };
    const bindCards = () => providers.querySelectorAll('.provider').forEach(card => {
      if (card.dataset.modalBound) return;
      card.dataset.modalBound = '1';
      card.addEventListener('click', () => openForCard(card));
    });
    styleRoleFilter();
    bindCards();
    new MutationObserver(() => { styleRoleFilter(); bindCards(); }).observe(providers.parentElement, { childList: true, subtree: true });
  }

  window.addEventListener('load', init);
})();
