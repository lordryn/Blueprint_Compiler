async function refreshCsrfToken() {
  const response = await fetch('/auth/session', { credentials: 'include' });
  if (!response.ok) {
    window.location.href = '/login';
    throw new Error('session expired');
  }
  const payload = await response.json();
  const csrfToken = String(payload.csrfToken || '').trim();
  window.__trainingCsrfToken = csrfToken;
  return csrfToken;
}

async function requestJson(url, options = {}) {
  const method = String(options.method || 'GET').toUpperCase();
  const isWrite = method !== 'GET';
  let csrfToken = String(window.__trainingCsrfToken || '').trim();
  if (isWrite && !csrfToken) {
    csrfToken = await refreshCsrfToken();
  }

  const response = await fetch(url, {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(isWrite && csrfToken ? { 'x-csrf-token': csrfToken } : {}),
      ...(options.headers || {})
    }
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const message = String(payload?.error || `request failed (${response.status})`).trim();
    throw new Error(message);
  }

  return payload;
}

async function requestJsonOptional(url, options = {}) {
  try {
    return await requestJson(url, options);
  } catch {
    return null;
  }
}

function roleFlags(session) {
  const role = String(session?.membership?.role || '').trim().toLowerCase();
  const isOwner = Boolean(session?.membership?.isOwner);
  const canCreate = isOwner || role === 'command' || role === 'conclave';
  const canAssign = canCreate || role === 'officer' || role === 'non_commissioned_officer';
  const canManage = canCreate;
  const canRemove = isOwner || role === 'command';
  const canViewLog = canCreate;
  return { role, isOwner, canCreate, canAssign, canManage, canRemove, canViewLog };
}

function formatRoleLabel(role, isOwner = false) {
  const normalized = String(role || 'enlisted')
    .trim()
    .toLowerCase()
    .replace(/_/g, ' ');
  const titled = normalized.replace(/\b\w/g, (char) => char.toUpperCase());
  return isOwner ? `${titled}, Owner` : titled;
}

const DEPARTMENT_DISPLAY_ORDER = [
  '',
  'Department of Terrestrial Warfare',
  'Department of Aerospace Warfare',
  'Ministry of Recruitment',
  'Ministry of Research and Development'
];
const DEPARTMENT_LABELS = {
  '': 'Order-Wide',
  'Department of Terrestrial Warfare': 'Department of Terrestrial Warfare',
  'Department of Aerospace Warfare': 'Department of Aerospace Warfare',
  'Ministry of Recruitment': 'Ministry of Recruitment',
  'Ministry of Research and Development': 'Ministry of Research and Development',
  'Ministry of Intelligence': 'Ministry of Intelligence'
};
const LEGACY_DIVISION_TO_DEPARTMENT = {
  'Fleet Division': 'Department of Aerospace Warfare',
  'Air Division': 'Department of Aerospace Warfare',
  'Shock Infantry Division': 'Department of Terrestrial Warfare'
};

const state = {
  session: null,
  flags: {
    role: 'enlisted',
    isOwner: false,
    canCreate: false,
    canAssign: false,
    canManage: false,
    canRemove: false,
    canViewLog: false
  },
  trainings: [],
  members: [],
  recipientSuggestions: [],
  selectedRecipients: [],
  prerequisiteSuggestions: {
    create: [],
    manage: []
  },
  selectedPrerequisites: {
    create: [],
    manage: []
  },
  trainingLog: [],
  logSuggestions: {
    recipient: [],
    instructor: [],
    training: []
  },
  logFilters: {
    mode: 'instructor',
    recipient: null,
    recipientQuery: '',
    instructor: null,
    instructorQuery: '',
    training: null,
    trainingQuery: ''
  },
  activeTab: 'training'
};

function setResult(id, message, type = 'info') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = String(message || '').trim();
  el.dataset.type = type;
}

function setLoadingResult(id, loading) {
  const el = document.getElementById(id);
  if (!el) return;
  el.dataset.loading = loading ? 'true' : 'false';
}

function normalizeManagingDepartment(value) {
  const normalized = String(value || '').trim();
  if (!normalized) return '';
  return LEGACY_DIVISION_TO_DEPARTMENT[normalized] || normalized;
}

function managingDepartmentLabel(training) {
  const department = normalizeManagingDepartment(
    training?.managingDepartment || training?.managingDivision || ''
  );
  return DEPARTMENT_LABELS[department] || DEPARTMENT_LABELS[''];
}

function isTrainingItem(item) {
  return String(item?.itemType || 'training').trim().toLowerCase() === 'training';
}

function isTrackableCatalogItem(item) {
  return item?.trackCompletion === true;
}

function trackableCatalogItems() {
  return state.trainings.filter((item) => isTrackableCatalogItem(item) && isTrainingItem(item));
}

function currentMember() {
  const membershipId = Number.parseInt(String(state.session?.membership?.id || ''), 10);
  return state.members.find((item) => Number.parseInt(String(item.id || ''), 10) === membershipId) || null;
}

function currentMemberDepartment() {
  const member = currentMember();
  return normalizeManagingDepartment(member?.department || member?.division || member?.ministry || '');
}

function prerequisiteItems(item) {
  return Array.isArray(item?.prerequisiteTrainings) ? item.prerequisiteTrainings : [];
}

function prerequisiteIds(item) {
  return Array.isArray(item?.prerequisiteTrainingIds)
    ? item.prerequisiteTrainingIds
    : prerequisiteItems(item).map((entry) => entry.id);
}

function documentsTabHash() {
  return '#knowledge-documents';
}

function tabFromHash() {
  const hash = String(window.location.hash || '').trim().toLowerCase();
  if (hash === '#documents' || hash === '#document-library' || hash === '#knowledge-documents') {
    return 'documents';
  }
  return 'training';
}

function syncHashWithActiveTab() {
  const nextHash = state.activeTab === 'documents' ? documentsTabHash() : '';
  const nextUrl = `${window.location.pathname}${window.location.search}${nextHash}`;
  window.history.replaceState(null, '', nextUrl);
}

function itemsForActiveTab() {
  return state.trainings.filter((item) => {
    if (state.activeTab === 'training') return isTrainingItem(item);
    return !isTrainingItem(item);
  });
}

function groupTrainingsByDepartment(items) {
  const groups = new Map();
  const sortedItems = [...items].sort((a, b) => {
    const rawA = normalizeManagingDepartment(a?.managingDepartment || a?.managingDivision || '');
    const rawB = normalizeManagingDepartment(b?.managingDepartment || b?.managingDivision || '');
    const safeAIdx = DEPARTMENT_DISPLAY_ORDER.indexOf(rawA);
    const safeBIdx = DEPARTMENT_DISPLAY_ORDER.indexOf(rawB);
    if (safeAIdx !== safeBIdx) return safeAIdx - safeBIdx;
    const deptA = DEPARTMENT_LABELS[rawA] || rawA || DEPARTMENT_LABELS[''];
    const deptB = DEPARTMENT_LABELS[rawB] || rawB || DEPARTMENT_LABELS[''];
    if (deptA !== deptB) return deptA.localeCompare(deptB);
    return String(a?.name || '').localeCompare(String(b?.name || ''));
  });
  sortedItems.forEach((item) => {
    const rawDepartment = normalizeManagingDepartment(item?.managingDepartment || item?.managingDivision || '');
    const label = DEPARTMENT_LABELS[rawDepartment] || rawDepartment || DEPARTMENT_LABELS[''];
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(item);
  });
  return groups;
}

function buildDivisionSelect() {
  ['createDivisionSelect', 'manageDivisionSelect'].forEach((id) => {
    const select = document.getElementById(id);
    if (!select) return;
    select.innerHTML = '';
    const noneOption = document.createElement('option');
    noneOption.value = '';
    noneOption.textContent = DEPARTMENT_LABELS[''];
    select.append(noneOption);
    DEPARTMENT_DISPLAY_ORDER.filter(Boolean).forEach((department) => {
      const option = document.createElement('option');
      option.value = department;
      option.textContent = DEPARTMENT_LABELS[department] || department;
      select.append(option);
    });
  });
}

function ensureSelectHasOption(select, value) {
  const normalized = String(value || '').trim();
  if (!select || !normalized) return;
  if ([...select.options].some((option) => option.value === normalized)) return;
  const option = document.createElement('option');
  option.value = normalized;
  option.textContent = DEPARTMENT_LABELS[normalized] || normalized;
  select.append(option);
}

function renderRecipients() {
  const pills = document.getElementById('recipientPills');
  if (!pills) return;
  pills.innerHTML = '';

  state.selectedRecipients.forEach((item) => {
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'pill';
    pill.textContent = `${item.name || item.username || `User ${item.id}`}`;
    pill.title = 'Remove recipient';
    pill.addEventListener('click', () => {
      state.selectedRecipients = state.selectedRecipients.filter((entry) => entry.id !== item.id);
      renderRecipients();
    });
    pills.append(pill);
  });
}

function prerequisiteContextIds(context) {
  return {
    searchInput: `${context}PrerequisiteSearchInput`,
    suggestList: `${context}PrerequisiteSuggestList`,
    pills: `${context}PrerequisitePills`,
    textarea: `${context}PrerequisitesInput`,
    result: `${context}Result`
  };
}

function selectedPrerequisitesFor(context) {
  return state.selectedPrerequisites[context] || [];
}

function setSelectedPrerequisites(context, items) {
  state.selectedPrerequisites[context] = Array.isArray(items) ? items : [];
}

function selectedManageTrainingId() {
  const select = document.getElementById('manageTrainingSelect');
  return Number.parseInt(String(select?.value || ''), 10);
}

function renderPrerequisitePills(context) {
  const ids = prerequisiteContextIds(context);
  const pills = document.getElementById(ids.pills);
  if (!pills) return;
  pills.innerHTML = '';

  selectedPrerequisitesFor(context).forEach((item) => {
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'pill';
    pill.textContent = item.name || `Training ${item.id}`;
    pill.title = 'Remove prerequisite';
    pill.addEventListener('click', () => {
      setSelectedPrerequisites(
        context,
        selectedPrerequisitesFor(context).filter((entry) => entry.id !== item.id)
      );
      renderPrerequisitePills(context);
    });
    pills.append(pill);
  });
}

function renderPrerequisiteSuggestions(context) {
  const ids = prerequisiteContextIds(context);
  const list = document.getElementById(ids.suggestList);
  if (!list) return;
  list.innerHTML = '';

  const items = Array.isArray(state.prerequisiteSuggestions[context]) ? state.prerequisiteSuggestions[context] : [];
  if (!items.length) {
    list.classList.add('hidden');
    return;
  }

  items.forEach((item) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'suggestion-item';
    button.textContent = `${item.name || 'Unnamed Training'} (${managingDepartmentLabel(item)})`;
    button.addEventListener('click', () => {
      const selected = selectedPrerequisitesFor(context);
      if (!selected.some((entry) => entry.id === item.id)) {
        setSelectedPrerequisites(context, [...selected, item]);
      }
      state.prerequisiteSuggestions[context] = [];
      const input = document.getElementById(ids.searchInput);
      if (input) input.value = '';
      renderPrerequisiteSuggestions(context);
      renderPrerequisitePills(context);
    });
    list.append(button);
  });

  list.classList.remove('hidden');
}

function searchPrerequisites(context, query) {
  const trimmed = String(query || '').trim().toLowerCase();
  if (!trimmed) {
    state.prerequisiteSuggestions[context] = [];
    renderPrerequisiteSuggestions(context);
    return;
  }

  const selectedIds = new Set(selectedPrerequisitesFor(context).map((item) => item.id));
  const manageTrainingId = context === 'manage' ? selectedManageTrainingId() : null;
  state.prerequisiteSuggestions[context] = trackableCatalogItems()
    .filter((item) => item.id !== manageTrainingId)
    .filter((item) => !selectedIds.has(item.id))
    .filter((item) => {
      const name = String(item.name || '').toLowerCase();
      const code = String(item.code || '').toLowerCase();
      return name.includes(trimmed) || code.includes(trimmed);
    })
    .slice(0, 8);
  renderPrerequisiteSuggestions(context);
}

function convertLegacyPrerequisites(context) {
  const ids = prerequisiteContextIds(context);
  const textarea = document.getElementById(ids.textarea);
  const lines = String(textarea?.value || '')
    .split(/\n+/)
    .map((line) => line.trim().replace(/[.]+$/g, '').trim())
    .filter((line) => line && !new Set(['none', 'n/a']).has(line.toLowerCase()));
  if (!lines.length) {
    setResult(ids.result, 'No prerequisite course names to convert.', 'error');
    return;
  }

  const selected = [...selectedPrerequisitesFor(context)];
  const selectedIds = new Set(selected.map((item) => item.id));
  const byName = new Map(trackableCatalogItems().map((item) => [String(item.name || '').trim().toLowerCase(), item]));
  const unmatched = [];
  lines.forEach((line) => {
    const item = byName.get(line.toLowerCase());
    if (!item) {
      unmatched.push(line);
      return;
    }
    if (!selectedIds.has(item.id)) {
      selected.push(item);
      selectedIds.add(item.id);
    }
  });

  setSelectedPrerequisites(context, selected);
  renderPrerequisitePills(context);
  setResult(
    ids.result,
    unmatched.length
      ? `Converted ${lines.length - unmatched.length}. Unmatched: ${unmatched.join(', ')}.`
      : `Converted ${lines.length} prerequisite${lines.length === 1 ? '' : 's'}.`,
    unmatched.length ? 'error' : 'success'
  );
}

function prerequisitePayloadFor(context) {
  const selected = selectedPrerequisitesFor(context);
  const ids = prerequisiteContextIds(context);
  const textarea = document.getElementById(ids.textarea);
  const selectedNames = selected.map((item) => item.name).filter(Boolean);
  const legacyText = String(textarea?.value || '').trim();
  return {
    prerequisiteTrainingIds: selected.map((item) => item.id),
    prerequisites: selectedNames.length ? selectedNames.join('\n') : legacyText
  };
}

function renderRecipientSuggestions() {
  const list = document.getElementById('recipientSuggestList');
  if (!list) return;
  list.innerHTML = '';

  if (!state.recipientSuggestions.length) {
    list.classList.add('hidden');
    return;
  }

  state.recipientSuggestions.forEach((item) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'suggestion-item';
    const rank = String(item.rankAbbreviation || '').trim();
    const name = String(item.name || item.username || '').trim();
    button.textContent = rank ? `${rank}, ${name}` : name;
    button.addEventListener('click', () => {
      const exists = state.selectedRecipients.some((entry) => entry.id === item.id);
      if (!exists) {
        state.selectedRecipients.push(item);
      }
      state.recipientSuggestions = [];
      const input = document.getElementById('recipientSearchInput');
      if (input) input.value = '';
      renderRecipientSuggestions();
      renderRecipients();
    });
    list.append(button);
  });

  list.classList.remove('hidden');
}

function formatLogMemberLabel(item) {
  const rank = String(item?.rankAbbreviation || '').trim();
  const name = String(item?.name || item?.username || '').trim();
  return rank ? `${rank}, ${name}` : name;
}

function setLogSuggestionItems(kind, items) {
  state.logSuggestions[kind] = Array.isArray(items) ? items : [];
}

function clearInactiveLogFilters(activeKind) {
  ['recipient', 'instructor', 'training'].forEach((kind) => {
    if (kind === activeKind) return;
    state.logFilters[kind] = null;
    state.logFilters[`${kind}Query`] = '';
    setLogSuggestionItems(kind, []);
  });
}

function logSearchPlaceholder(mode) {
  if (mode === 'instructor') return 'Search instructor...';
  if (mode === 'training') return 'Search catalog item...';
  return 'Search recipient...';
}

function updateLogSearchPlaceholder() {
  const input = document.getElementById('logSearchInput');
  if (!input) return;
  input.placeholder = logSearchPlaceholder(state.logFilters.mode);
}

function renderLogSuggestions(kind) {
  const list = document.getElementById('logSuggestList');
  if (!list) return;
  list.innerHTML = '';

  const items = Array.isArray(state.logSuggestions[kind]) ? state.logSuggestions[kind] : [];
  if (!items.length) {
    list.classList.add('hidden');
    return;
  }

  items.forEach((item) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'suggestion-item';
    button.textContent = kind === 'training'
      ? `${item.name || 'Unnamed Item'}${item.managingDepartment ? ` (${managingDepartmentLabel(item)})` : ''}`
      : formatLogMemberLabel(item);
    button.addEventListener('click', async () => {
      const input = document.getElementById('logSearchInput');
      if (kind === 'training') {
        state.logFilters.training = item;
        state.logFilters.trainingQuery = String(item.name || '').trim();
      } else {
        state.logFilters[kind] = item;
        state.logFilters[`${kind}Query`] = String(item.name || item.username || '').trim();
      }
      if (input) input.value = state.logFilters[`${kind}Query`];
      setLogSuggestionItems(kind, []);
      renderLogSuggestions(kind);
      await loadTrainingLog();
    });
    list.append(button);
  });

  list.classList.remove('hidden');
}

function formatTimestamp(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit'
  }).format(date);
}

function renderTrainingLog() {
  const list = document.getElementById('trainingLogList');
  if (!list) return;
  list.innerHTML = '';

  const items = Array.isArray(state.trainingLog) ? state.trainingLog : [];
  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'No completion records match the current filters.';
    list.append(empty);
    return;
  }

  items.forEach((entry) => {
    const row = document.createElement('div');
    row.className = 'log-row';

    const completedAt = document.createElement('span');
    completedAt.className = 'log-cell';
    completedAt.textContent = formatTimestamp(entry.completedAt) || 'Unknown';

    const recipient = document.createElement('span');
    recipient.className = 'log-cell';
    recipient.textContent = formatLogMemberLabel(entry.recipient || {});

    const instructor = document.createElement('span');
    instructor.className = 'log-cell';
    instructor.textContent = String(
      entry?.instructor?.name
      || entry?.instructor?.username
      || entry?.instructor?.text
      || 'Unknown'
    ).trim();

    const training = document.createElement('span');
    training.className = 'log-cell';
    training.textContent = entry?.training?.name || 'Unnamed Item';

    const department = document.createElement('span');
    department.className = 'log-cell';
    department.textContent = managingDepartmentLabel(entry?.training || {});

    row.append(completedAt, recipient, instructor, training, department);
    list.append(row);
  });
}

async function searchRecipients(query) {
  const trimmed = String(query || '').trim();
  if (!trimmed || !state.flags.canAssign) {
    state.recipientSuggestions = [];
    renderRecipientSuggestions();
    return;
  }

  try {
    const payload = await requestJson(`/app/api/trainings/member-search?q=${encodeURIComponent(trimmed)}`);
    const selected = new Set(state.selectedRecipients.map((item) => item.id));
    state.recipientSuggestions = (Array.isArray(payload?.items) ? payload.items : [])
      .filter((item) => !selected.has(item.id));
    renderRecipientSuggestions();
  } catch {
    state.recipientSuggestions = [];
    renderRecipientSuggestions();
  }
}

async function searchLogMembers(kind, query) {
  const trimmed = String(query || '').trim();
  if (!state.flags.canViewLog || !trimmed) {
    setLogSuggestionItems(kind, []);
    renderLogSuggestions(kind);
    return;
  }

  try {
    const payload = await requestJson(`/app/api/trainings/member-search?q=${encodeURIComponent(trimmed)}`);
    setLogSuggestionItems(kind, Array.isArray(payload?.items) ? payload.items : []);
  } catch {
    setLogSuggestionItems(kind, []);
  }
  renderLogSuggestions(kind);
}

function searchLogTrainings(query) {
  const trimmed = String(query || '').trim().toLowerCase();
  if (!state.flags.canViewLog || !trimmed) {
    setLogSuggestionItems('training', []);
    renderLogSuggestions('training');
    return;
  }

  const items = trackableCatalogItems()
    .filter((item) => {
      const name = String(item?.name || '').toLowerCase();
      const code = String(item?.code || '').toLowerCase();
      return name.includes(trimmed) || code.includes(trimmed);
    })
    .slice(0, 8);
  setLogSuggestionItems('training', items);
  renderLogSuggestions('training');
}

function clearLogFilter(kind, clearInput = true) {
  state.logFilters[kind] = null;
  state.logFilters[`${kind}Query`] = '';
  setLogSuggestionItems(kind, []);
  renderLogSuggestions(kind);
  if (!clearInput) return;
  const input = document.getElementById('logSearchInput');
  if (input) input.value = '';
}

async function loadTrainingLog() {
  if (!state.flags.canViewLog) return;

  const params = new URLSearchParams();
  if (state.logFilters.recipient?.id) {
    params.set('recipientUserId', String(state.logFilters.recipient.id));
  } else if (state.logFilters.recipientQuery) {
    params.set('recipientQuery', state.logFilters.recipientQuery);
  }
  if (state.logFilters.instructor?.id) {
    params.set('instructorUserId', String(state.logFilters.instructor.id));
  } else if (state.logFilters.instructorQuery) {
    params.set('instructorQuery', state.logFilters.instructorQuery);
  }
  if (state.logFilters.training?.id) {
    params.set('trainingDefinitionId', String(state.logFilters.training.id));
  } else if (state.logFilters.trainingQuery) {
    params.set('trainingQuery', state.logFilters.trainingQuery);
  }

  try {
    const payload = await requestJson(`/app/api/trainings/log?${params.toString()}`);
    state.trainingLog = Array.isArray(payload?.items) ? payload.items : [];
    renderTrainingLog();
  } catch (error) {
    state.trainingLog = [];
    renderTrainingLog();
  }
}

function renderAssignTrainingOptions() {
  const select = document.getElementById('assignTrainingSelect');
  if (!select) return;
  select.innerHTML = '';

  trackableCatalogItems().forEach((training) => {
    const option = document.createElement('option');
    option.value = String(training.id || '');
    option.textContent = `${training.name} (${managingDepartmentLabel(training)})`;
    select.append(option);
  });

  if (!trackableCatalogItems().length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No assignable items defined yet';
    select.append(option);
  }
}

function renderManageTrainingOptions() {
  const select = document.getElementById('manageTrainingSelect');
  if (!select) return;
  const currentValue = String(select.value || '').trim();
  select.innerHTML = '';

  itemsForActiveTab().forEach((training) => {
    const option = document.createElement('option');
    option.value = String(training.id || '');
    option.textContent = `${training.name} (${managingDepartmentLabel(training)})`;
    select.append(option);
  });

  if (!itemsForActiveTab().length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = state.activeTab === 'training'
      ? 'No training items defined yet'
      : 'No knowledge documents defined yet';
    select.append(option);
    populateManageTrainingFields(null);
    return;
  }

  const visibleItems = itemsForActiveTab();
  select.value = visibleItems.some((training) => String(training.id) === currentValue)
    ? currentValue
    : String(visibleItems[0].id || '');
  populateManageTrainingFields(select.value);
}

function populateManageTrainingFields(trainingIdRaw) {
  const trainingId = Number.parseInt(String(trainingIdRaw || ''), 10);
  const selected = itemsForActiveTab().find((item) => Number.parseInt(String(item.id || ''), 10) === trainingId) || null;
  const nameInput = document.getElementById('manageNameInput');
  const divisionSelect = document.getElementById('manageDivisionSelect');
  const descriptionInput = document.getElementById('manageDescriptionInput');
  const prerequisitesInput = document.getElementById('managePrerequisitesInput');
  const documentLinkInput = document.getElementById('manageDocumentLinkInput');
  const saveBtn = document.getElementById('saveManageTrainingBtn');
  const removeBtn = document.getElementById('removeManageTrainingBtn');

  if (nameInput) nameInput.value = selected?.name || '';
  if (divisionSelect) {
    const selectedValue = normalizeManagingDepartment(selected?.managingDepartment || selected?.managingDivision || '');
    ensureSelectHasOption(divisionSelect, selectedValue);
    divisionSelect.value = selectedValue;
  }
  if (descriptionInput) descriptionInput.value = selected?.description || '';
  if (prerequisitesInput) prerequisitesInput.value = selected?.prerequisites || '';
  if (documentLinkInput) documentLinkInput.value = selected?.documentLink || '';
  setSelectedPrerequisites('manage', prerequisiteItems(selected).map((item) => ({
    ...item,
    managingDepartment: item.managingDepartment || normalizeManagingDepartment(item.managingDivision || '')
  })));
  renderPrerequisitePills('manage');
  const hasTraining = Boolean(selected);
  if (saveBtn) saveBtn.disabled = !hasTraining;
  if (removeBtn) removeBtn.disabled = !hasTraining;
}

function renderCatalog() {
  const trainingList = document.getElementById('trainingCatalogList');
  const referenceList = document.getElementById('referenceCatalogList');
  const trainingCounter = document.getElementById('trainingCatalogCount');
  const referenceCounter = document.getElementById('referenceCatalogCount');
  const trainingPanel = document.getElementById('trainingCatalogPanel');
  const referencePanel = document.getElementById('documentLibraryPanel');
  const tabTraining = document.getElementById('tabTrainingCatalog');
  const tabDocuments = document.getElementById('tabDocumentLibrary');
  if (!trainingList || !referenceList || !trainingPanel || !referencePanel) return;

  const trainingItems = state.trainings.filter((item) => isTrainingItem(item));
  const referenceItems = state.trainings.filter((item) => !isTrainingItem(item));

  if (trainingCounter) {
    trainingCounter.textContent = `${trainingItems.length} ${trainingItems.length === 1 ? 'Item' : 'Items'}`;
  }
  if (referenceCounter) {
    referenceCounter.textContent = `${referenceItems.length} ${referenceItems.length === 1 ? 'Item' : 'Items'}`;
  }

  trainingPanel.classList.toggle('hidden', state.activeTab !== 'training');
  referencePanel.classList.toggle('hidden', state.activeTab !== 'documents');
  if (tabTraining) {
    tabTraining.setAttribute('aria-selected', String(state.activeTab === 'training'));
    tabTraining.classList.toggle('is-active', state.activeTab === 'training');
  }
  if (tabDocuments) {
    tabDocuments.setAttribute('aria-selected', String(state.activeTab === 'documents'));
    tabDocuments.classList.toggle('is-active', state.activeTab === 'documents');
  }

  renderCatalogSection(trainingList, trainingItems, 'No training items exist yet.');
  renderCatalogSection(referenceList, referenceItems, 'No knowledge documents exist yet.');
}

function renderCatalogSection(list, items, emptyMessage) {
  list.innerHTML = '';

  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = emptyMessage;
    list.append(empty);
    return;
  }

  const groups = groupTrainingsByDepartment(items);
  groups.forEach((groupItems, departmentName) => {
    const divider = document.createElement('div');
    divider.className = 'catalog-group-divider';
    divider.textContent = departmentName;
    list.append(divider);

    groupItems.forEach((item) => {
      const details = document.createElement('details');
      details.className = 'training-item';

      const summary = document.createElement('summary');
      summary.className = 'training-summary';

      const title = document.createElement('span');
      title.className = 'training-name';
      title.textContent = item.name || 'Unnamed Item';

      const division = document.createElement('span');
      division.className = 'training-division';
      division.textContent = departmentName;

      const docCell = document.createElement('span');
      docCell.className = 'training-doc-cell';
      const documentLink = String(item.documentLink || '').trim();
      if (documentLink) {
        const docLink = document.createElement('a');
        docLink.className = 'training-doc-link';
        docLink.href = documentLink;
        docLink.target = '_blank';
        docLink.rel = 'noopener noreferrer';
        const ctaLabel = isTrainingItem(item) ? 'Open Training Document' : 'Open Knowledge Document';
        docLink.setAttribute('aria-label', `${ctaLabel} for ${item.name || 'catalog item'}`);
        docLink.textContent = `${ctaLabel} ↗`;
        docLink.addEventListener('click', (event) => event.stopPropagation());
        docCell.append(docLink);
      }

      summary.append(title, division, docCell);

      const body = document.createElement('div');
      body.className = 'training-body';

      const descriptionLabel = document.createElement('p');
      descriptionLabel.className = 'training-label';
      descriptionLabel.textContent = 'Description';

      const description = document.createElement('p');
      description.className = 'training-text';
      description.textContent = String(item.description || '').trim() || 'No description provided.';
      body.append(descriptionLabel, description);

      if (isTrainingItem(item)) {
        const prerequisitesLabel = document.createElement('p');
        prerequisitesLabel.className = 'training-label';
        prerequisitesLabel.textContent = 'Prerequisites / Requirements';

        const prerequisites = document.createElement('div');
        prerequisites.className = 'training-text';
        const structuredPrerequisites = prerequisiteItems(item);
        if (structuredPrerequisites.length) {
          const prereqList = document.createElement('ul');
          prereqList.className = 'training-prerequisite-list';
          structuredPrerequisites.forEach((prerequisite) => {
            const line = document.createElement('li');
            line.textContent = prerequisite.name || `Training ${prerequisite.id}`;
            prereqList.append(line);
          });
          prerequisites.append(prereqList);
        } else {
          prerequisites.textContent = String(item.prerequisites || '').trim() || 'No prerequisites listed.';
        }

        body.append(prerequisitesLabel, prerequisites);
      }

      details.append(summary, body);
      list.append(details);
    });
  });
}

function switchCatalogTab(tab) {
  state.activeTab = tab === 'documents' ? 'documents' : 'training';
  syncHashWithActiveTab();
  applyCatalogContextCopy();
  renderCatalog();
  renderManageTrainingOptions();
  applyRoleVisibility();
}

function applyCatalogContextCopy() {
  const isTrainingTab = state.activeTab === 'training';
  const pageTitle = document.getElementById('catalogPageTitle');
  const openCreateButton = document.getElementById('openCreateModalBtn');
  const openManageButton = document.getElementById('openManageModalBtn');
  const createTitle = document.getElementById('createTrainingTitle');
  const manageTitle = document.getElementById('manageTrainingTitle');
  const createButton = document.getElementById('createTrainingBtn');
  const createNameField = document.getElementById('createNameField');
  const createNameInput = document.getElementById('createNameInput');
  const createDescriptionInput = document.getElementById('createDescriptionInput');
  const createRequirementsField = document.getElementById('createRequirementsField');
  const createPrerequisitesInput = document.getElementById('createPrerequisitesInput');
  const createDivisionSelect = document.getElementById('createDivisionSelect');
  const manageItemField = document.getElementById('manageItemField');
  const manageNameField = document.getElementById('manageNameField');
  const manageRequirementsField = document.getElementById('manageRequirementsField');
  const manageDescriptionInput = document.getElementById('manageDescriptionInput');
  const managePrerequisitesInput = document.getElementById('managePrerequisitesInput');
  const manageDivisionSelect = document.getElementById('manageDivisionSelect');
  const manageRemoveButton = document.getElementById('removeManageTrainingBtn');
  const trainingHeading = document.getElementById('trainingCatalogHeading');
  const documentHeading = document.getElementById('documentLibraryHeading');

  if (pageTitle) pageTitle.textContent = 'Training/Knowledge Catalog';
  if (trainingHeading) trainingHeading.textContent = 'Training Catalog';
  if (documentHeading) documentHeading.textContent = 'Knowledge Documents';
  if (openCreateButton) openCreateButton.textContent = isTrainingTab ? 'Create Training' : 'Create Document';
  if (openManageButton) openManageButton.textContent = isTrainingTab ? 'Manage Training' : 'Manage Document';
  if (createTitle) createTitle.textContent = isTrainingTab ? 'Create Training' : 'Create Document';
  if (manageTitle) manageTitle.textContent = isTrainingTab ? 'Manage Training' : 'Manage Document';
  if (createButton) createButton.textContent = isTrainingTab ? 'Create Training' : 'Create Document';
  if (createNameField) createNameField.firstChild.textContent = isTrainingTab ? 'Training Name' : 'Document Title';
  if (createNameInput) createNameInput.placeholder = isTrainingTab ? 'e.g. Basic Training' : 'e.g. Flight SOP';
  if (createDescriptionInput) createDescriptionInput.placeholder = isTrainingTab ? 'What this training covers...' : 'What this document covers...';
  if (createRequirementsField) createRequirementsField.classList.toggle('hidden', !isTrainingTab);
  if (createPrerequisitesInput && !isTrainingTab) createPrerequisitesInput.value = '';
  if (createDivisionSelect) createDivisionSelect.setAttribute('aria-label', 'Owning department or ministry');
  if (manageItemField) manageItemField.firstChild.textContent = isTrainingTab ? 'Training' : 'Document';
  if (manageNameField) manageNameField.firstChild.textContent = isTrainingTab ? 'Training Name' : 'Document Title';
  if (manageRequirementsField) manageRequirementsField.classList.toggle('hidden', !isTrainingTab);
  if (manageDescriptionInput) manageDescriptionInput.placeholder = isTrainingTab ? 'What this training covers...' : 'What this document covers...';
  if (managePrerequisitesInput && !isTrainingTab) managePrerequisitesInput.value = '';
  if (manageDivisionSelect) manageDivisionSelect.setAttribute('aria-label', 'Owning department or ministry');
  if (manageRemoveButton) manageRemoveButton.textContent = isTrainingTab ? 'Remove Training' : 'Remove Document';
}

async function loadTrainings() {
  const payload = await requestJson('/app/api/trainings/definitions');
  state.trainings = Array.isArray(payload?.items) ? payload.items : [];
  renderCatalog();
  renderAssignTrainingOptions();
  renderManageTrainingOptions();
  renderPrerequisitePills('create');
  renderPrerequisitePills('manage');
  if (state.flags.canViewLog) {
    searchLogTrainings(state.logFilters.trainingQuery);
  }
}

async function createTraining() {
  const nameInput = document.getElementById('createNameInput');
  const divisionSelect = document.getElementById('createDivisionSelect');
  const descriptionInput = document.getElementById('createDescriptionInput');
  const prerequisitesInput = document.getElementById('createPrerequisitesInput');
  const documentLinkInput = document.getElementById('createDocumentLinkInput');
  if (!nameInput || !divisionSelect || !descriptionInput || !prerequisitesInput || !documentLinkInput) return;

  const name = String(nameInput.value || '').trim();
  const itemType = state.activeTab === 'training' ? 'training' : 'reference';
  const managingDepartment = String(divisionSelect.value || '').trim();
  const trackCompletion = state.activeTab === 'training';
  const isTrainingTab = state.activeTab === 'training';
  const description = String(descriptionInput.value || '').trim();
  const prerequisitePayload = isTrainingTab ? prerequisitePayloadFor('create') : { prerequisiteTrainingIds: [], prerequisites: '' };
  const prerequisites = prerequisitePayload.prerequisites;
  const documentLink = String(documentLinkInput.value || '').trim();
  if (!name) {
    setResult('createResult', 'Item name is required.', 'error');
    return;
  }

  const button = document.getElementById('createTrainingBtn');
  if (button) button.disabled = true;
  setLoadingResult('createResult', true);
  setResult('createResult', 'Saving catalog item...', 'info');

  try {
    await requestJson('/app/api/trainings/definitions', {
      method: 'POST',
      body: JSON.stringify({
        name,
        itemType,
        trackCompletion,
        managingDepartment,
        description,
        prerequisites,
        prerequisiteTrainingIds: prerequisitePayload.prerequisiteTrainingIds,
        documentLink
      })
    });

    nameInput.value = '';
    descriptionInput.value = '';
    prerequisitesInput.value = '';
    setSelectedPrerequisites('create', []);
    renderPrerequisitePills('create');
    documentLinkInput.value = '';
    setResult('createResult', 'Catalog item created.', 'success');
    await loadTrainings();
  } catch (error) {
    setResult('createResult', error.message || 'Unable to create catalog item.', 'error');
  } finally {
    if (button) button.disabled = false;
    setLoadingResult('createResult', false);
  }
}

async function assignTraining() {
  const select = document.getElementById('assignTrainingSelect');
  if (!select) return;

  const trainingDefinitionId = Number.parseInt(String(select.value || ''), 10);
  if (!Number.isInteger(trainingDefinitionId) || trainingDefinitionId <= 0) {
    setResult('assignResult', 'Select an assignable item first.', 'error');
    return;
  }

  const recipientUserIds = state.selectedRecipients.map((item) => item.id);
  if (!recipientUserIds.length) {
    setResult('assignResult', 'Add at least one recipient.', 'error');
    return;
  }

  const button = document.getElementById('assignTrainingBtn');
  if (button) button.disabled = true;
  setLoadingResult('assignResult', true);
  setResult('assignResult', 'Logging completion assignments...', 'info');

  try {
    const payload = await requestJson('/app/api/trainings/assign', {
      method: 'POST',
      body: JSON.stringify({ trainingDefinitionId, recipientUserIds })
    });

    state.selectedRecipients = [];
    renderRecipients();
    setResult(
      'assignResult',
      `Assigned to ${payload?.recipientCount || recipientUserIds.length}. New completions logged: ${payload?.insertedCount || 0}.`,
      'success'
    );
    await loadTrainings();
    await loadTrainingLog();
  } catch (error) {
    setResult('assignResult', error.message || 'Unable to assign completion.', 'error');
  } finally {
    if (button) button.disabled = false;
    setLoadingResult('assignResult', false);
  }
}

function openConfirmDialog(message, onConfirm) {
  const modal = document.getElementById('confirmModal');
  const text = document.getElementById('confirmText');
  const okBtn = document.getElementById('confirmOkBtn');
  const cancelBtn = document.getElementById('confirmCancelBtn');
  if (!modal || !text || !okBtn || !cancelBtn) return;

  text.textContent = String(message || 'Are you sure?');
  modal.classList.remove('hidden');

  const cleanup = () => {
    okBtn.onclick = null;
    cancelBtn.onclick = null;
  };
  const close = () => {
    modal.classList.add('hidden');
    cleanup();
  };
  cancelBtn.onclick = close;
  okBtn.onclick = async () => {
    okBtn.disabled = true;
    try {
      await onConfirm();
      close();
    } finally {
      okBtn.disabled = false;
    }
  };
}

async function saveManagedTraining() {
  const select = document.getElementById('manageTrainingSelect');
  const nameInput = document.getElementById('manageNameInput');
  const divisionSelect = document.getElementById('manageDivisionSelect');
  const descriptionInput = document.getElementById('manageDescriptionInput');
  const prerequisitesInput = document.getElementById('managePrerequisitesInput');
  const documentLinkInput = document.getElementById('manageDocumentLinkInput');
  if (!select || !nameInput || !divisionSelect || !descriptionInput || !prerequisitesInput || !documentLinkInput) return;

  const trainingDefinitionId = Number.parseInt(String(select.value || ''), 10);
  if (!Number.isInteger(trainingDefinitionId) || trainingDefinitionId <= 0) {
    setResult('manageResult', 'Select a catalog item first.', 'error');
    return;
  }
  const name = String(nameInput.value || '').trim();
  const itemType = state.activeTab === 'training' ? 'training' : 'reference';
  const managingDepartment = String(divisionSelect.value || '').trim();
  const trackCompletion = state.activeTab === 'training';
  const isTrainingTab = state.activeTab === 'training';
  const description = String(descriptionInput.value || '').trim();
  const prerequisitePayload = isTrainingTab ? prerequisitePayloadFor('manage') : { prerequisiteTrainingIds: [], prerequisites: '' };
  const prerequisites = prerequisitePayload.prerequisites;
  const documentLink = String(documentLinkInput.value || '').trim();
  if (!name) {
    setResult('manageResult', 'Item name is required.', 'error');
    return;
  }

  const button = document.getElementById('saveManageTrainingBtn');
  if (button) button.disabled = true;
  setResult('manageResult', 'Saving changes...', 'info');
  try {
    await requestJson(`/app/api/trainings/definitions/${trainingDefinitionId}`, {
      method: 'PATCH',
      body: JSON.stringify({
        name,
        itemType,
        trackCompletion,
        managingDepartment,
        description,
        prerequisites,
        prerequisiteTrainingIds: prerequisitePayload.prerequisiteTrainingIds,
        documentLink
      })
    });
    setResult('manageResult', 'Catalog item updated.', 'success');
    await loadTrainings();
  } catch (error) {
    setResult('manageResult', error.message || 'Unable to update catalog item.', 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

async function removeManagedTraining() {
  const select = document.getElementById('manageTrainingSelect');
  if (!select) return;
  const trainingDefinitionId = Number.parseInt(String(select.value || ''), 10);
  if (!Number.isInteger(trainingDefinitionId) || trainingDefinitionId <= 0) {
    setResult('manageResult', 'Select a catalog item first.', 'error');
    return;
  }

  const button = document.getElementById('removeManageTrainingBtn');
  if (button) button.disabled = true;
  setResult('manageResult', 'Removing catalog item...', 'info');
  try {
    const payload = await requestJson(`/app/api/trainings/definitions/${trainingDefinitionId}`, {
      method: 'DELETE'
    });
    setResult('manageResult', `Catalog item removed. Cleared completions: ${payload?.removedCompletionCount || 0}.`, 'success');
    await loadTrainings();
    await loadTrainingLog();
  } catch (error) {
    setResult('manageResult', error.message || 'Unable to remove catalog item.', 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.remove('hidden');
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.add('hidden');
}

function topOpenModalId() {
  const modalIds = ['confirmModal', 'trainingLogModal', 'manageTrainingModal', 'assignTrainingModal', 'createTrainingModal'];
  for (const id of modalIds) {
    const modal = document.getElementById(id);
    if (modal && !modal.classList.contains('hidden')) return id;
  }
  return '';
}

function applyRoleVisibility() {
  const actionsPanel = document.getElementById('actionsPanel');
  const createButton = document.getElementById('openCreateModalBtn');
  const assignButton = document.getElementById('openAssignModalBtn');
  const manageButton = document.getElementById('openManageModalBtn');
  const trainingLogButton = document.getElementById('openTrainingLogModalBtn');
  const removeBtn = document.getElementById('removeManageTrainingBtn');
  const canUseActions = state.flags.canCreate || state.flags.canAssign || state.flags.canManage || state.flags.canViewLog;

  if (actionsPanel) {
    actionsPanel.classList.toggle('hidden', !canUseActions);
  }
  if (createButton) {
    createButton.classList.toggle('hidden', !state.flags.canCreate);
  }
  if (assignButton) {
    assignButton.classList.toggle('hidden', !state.flags.canAssign || state.activeTab !== 'training');
  }
  if (manageButton) {
    manageButton.classList.toggle('hidden', !state.flags.canManage);
  }
  if (trainingLogButton) {
    trainingLogButton.classList.toggle('hidden', !state.flags.canViewLog || state.activeTab !== 'training');
  }
  if (removeBtn) {
    removeBtn.classList.toggle('hidden', !state.flags.canRemove);
  }
}

async function init() {
  try {
    const session = await requestJson('/auth/session');
    window.__trainingCsrfToken = String(session.csrfToken || '');
    state.session = session;
    state.flags = roleFlags(session);
    state.activeTab = tabFromHash();
    const membersPayload = await requestJsonOptional('/app/api/members');
    state.members = Array.isArray(membersPayload?.items) ? membersPayload.items : [];

    const whoami = document.getElementById('whoami');
    if (whoami) {
      const name = session?.user?.name || session?.user?.username || 'Member';
      whoami.textContent = `${name} | ${formatRoleLabel(state.flags.role, state.flags.isOwner)}`;
    }

    buildDivisionSelect();
    applyCatalogContextCopy();
    applyRoleVisibility();
    await loadTrainings();
    await loadTrainingLog();

    const createButton = document.getElementById('createTrainingBtn');
    createButton?.addEventListener('click', createTraining);

    const assignButton = document.getElementById('assignTrainingBtn');
    assignButton?.addEventListener('click', assignTraining);

    const openCreateModalBtn = document.getElementById('openCreateModalBtn');
    openCreateModalBtn?.addEventListener('click', () => openModal('createTrainingModal'));

    const openAssignModalBtn = document.getElementById('openAssignModalBtn');
    openAssignModalBtn?.addEventListener('click', () => openModal('assignTrainingModal'));

    const openManageModalBtn = document.getElementById('openManageModalBtn');
    openManageModalBtn?.addEventListener('click', () => {
      renderManageTrainingOptions();
      openModal('manageTrainingModal');
    });

    const openTrainingLogModalBtn = document.getElementById('openTrainingLogModalBtn');
    openTrainingLogModalBtn?.addEventListener('click', async () => {
      await loadTrainingLog();
      openModal('trainingLogModal');
    });

    const closeCreateModalBtn = document.getElementById('closeCreateModalBtn');
    closeCreateModalBtn?.addEventListener('click', () => closeModal('createTrainingModal'));
    const closeCreateModalXBtn = document.getElementById('closeCreateModalXBtn');
    closeCreateModalXBtn?.addEventListener('click', () => closeModal('createTrainingModal'));

    const closeAssignModalBtn = document.getElementById('closeAssignModalBtn');
    closeAssignModalBtn?.addEventListener('click', () => closeModal('assignTrainingModal'));
    const closeAssignModalXBtn = document.getElementById('closeAssignModalXBtn');
    closeAssignModalXBtn?.addEventListener('click', () => closeModal('assignTrainingModal'));

    const closeManageModalBtn = document.getElementById('closeManageModalBtn');
    closeManageModalBtn?.addEventListener('click', () => closeModal('manageTrainingModal'));
    const closeManageModalXBtn = document.getElementById('closeManageModalXBtn');
    closeManageModalXBtn?.addEventListener('click', () => closeModal('manageTrainingModal'));

    const closeTrainingLogModalBtn = document.getElementById('closeTrainingLogModalBtn');
    closeTrainingLogModalBtn?.addEventListener('click', () => closeModal('trainingLogModal'));
    const closeTrainingLogModalXBtn = document.getElementById('closeTrainingLogModalXBtn');
    closeTrainingLogModalXBtn?.addEventListener('click', () => closeModal('trainingLogModal'));

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      const openModalId = topOpenModalId();
      if (!openModalId) return;
      closeModal(openModalId);
    });

    const manageTrainingSelect = document.getElementById('manageTrainingSelect');
    manageTrainingSelect?.addEventListener('change', () => {
      populateManageTrainingFields(manageTrainingSelect.value);
    });

    const saveManageTrainingBtn = document.getElementById('saveManageTrainingBtn');
    saveManageTrainingBtn?.addEventListener('click', () => {
      openConfirmDialog('Save these catalog item changes?', saveManagedTraining);
    });

    const removeManageTrainingBtn = document.getElementById('removeManageTrainingBtn');
    removeManageTrainingBtn?.addEventListener('click', () => {
      openConfirmDialog('Remove this catalog item and clear all existing completions?', removeManagedTraining);
    });

    document.getElementById('tabTrainingCatalog')?.addEventListener('click', () => switchCatalogTab('training'));
    document.getElementById('tabDocumentLibrary')?.addEventListener('click', () => switchCatalogTab('documents'));
    window.addEventListener('hashchange', () => {
      const nextTab = tabFromHash();
      if (nextTab === state.activeTab) return;
      state.activeTab = nextTab;
      applyCatalogContextCopy();
      renderCatalog();
      renderManageTrainingOptions();
      applyRoleVisibility();
    });

    const recipientInput = document.getElementById('recipientSearchInput');
    recipientInput?.addEventListener('input', async (event) => {
      await searchRecipients(event.target.value);
    });
    recipientInput?.addEventListener('blur', () => {
      window.setTimeout(() => {
        state.recipientSuggestions = [];
        renderRecipientSuggestions();
      }, 120);
    });

    ['create', 'manage'].forEach((context) => {
      const ids = prerequisiteContextIds(context);
      const input = document.getElementById(ids.searchInput);
      input?.addEventListener('input', (event) => {
        searchPrerequisites(context, event.target.value);
      });
      input?.addEventListener('blur', () => {
        window.setTimeout(() => {
          state.prerequisiteSuggestions[context] = [];
          renderPrerequisiteSuggestions(context);
        }, 120);
      });
    });
    document.getElementById('createConvertPrerequisitesBtn')?.addEventListener('click', () => {
      convertLegacyPrerequisites('create');
    });
    document.getElementById('manageConvertPrerequisitesBtn')?.addEventListener('click', () => {
      convertLegacyPrerequisites('manage');
    });

    const logSearchModeSelect = document.getElementById('logSearchModeSelect');
    const logSearchInput = document.getElementById('logSearchInput');
    updateLogSearchPlaceholder();
    logSearchModeSelect?.addEventListener('change', async (event) => {
      state.logFilters.mode = String(event.target.value || 'recipient');
      clearInactiveLogFilters(state.logFilters.mode);
      if (logSearchInput) {
        logSearchInput.value = state.logFilters[`${state.logFilters.mode}Query`] || '';
      }
      updateLogSearchPlaceholder();
      renderLogSuggestions(state.logFilters.mode);
      await loadTrainingLog();
    });
    logSearchInput?.addEventListener('input', async (event) => {
      const kind = state.logFilters.mode;
      const value = String(event.target.value || '');
      clearInactiveLogFilters(kind);
      if (state.logFilters[kind] && value.trim() !== state.logFilters[`${kind}Query`]) {
        state.logFilters[kind] = null;
      }
      state.logFilters[`${kind}Query`] = value.trim();
      if (kind === 'training') {
        searchLogTrainings(value);
      } else {
        await searchLogMembers(kind, value);
      }
      if (!value.trim()) {
        await loadTrainingLog();
      }
    });
    logSearchInput?.addEventListener('change', async () => {
      await loadTrainingLog();
    });
    logSearchInput?.addEventListener('blur', () => {
      window.setTimeout(() => {
        setLogSuggestionItems(state.logFilters.mode, []);
        renderLogSuggestions(state.logFilters.mode);
      }, 120);
    });

    const clearTrainingLogFiltersBtn = document.getElementById('clearTrainingLogFiltersBtn');
    clearTrainingLogFiltersBtn?.addEventListener('click', async () => {
      ['recipient', 'instructor', 'training'].forEach((kind) => clearLogFilter(kind, false));
      if (logSearchInput) logSearchInput.value = '';
      await loadTrainingLog();
    });
  } catch (error) {
    const whoami = document.getElementById('whoami');
    if (whoami) whoami.textContent = String(error.message || 'Unable to load training/knowledge catalog');
  }
}

init();
