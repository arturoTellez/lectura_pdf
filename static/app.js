document.addEventListener('DOMContentLoaded', () => {
    // Navigation
    const navButtons = document.querySelectorAll('.nav-btn');
    const sections = document.querySelectorAll('.section');
    const sectionTitle = document.getElementById('section-title');

    navButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const target = btn.dataset.section;
            navButtons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            sections.forEach(s => s.classList.add('hidden'));
            document.getElementById(`${target}-section`).classList.remove('hidden');
            sectionTitle.textContent = btn.textContent.trim();

            if (target === 'dashboard') loadDashboard();
            if (target === 'reports') loadReports();
            if (target === 'recurrence') loadRecurrence();
        });
    });

    // Upload Matrix Logic
    const bankFileInput = document.getElementById('bank-file-input');
    let currentParser = null;
    let currentContext = { month: null, bank: null, cell: null };

    async function loadUploadMatrix() {
        try {
            const res = await fetch('/upload/matrix');
            const data = await res.json();
            renderUploadMatrix(data);
        } catch (e) {
            console.error('Error loading matrix', e);
        }
    }

    function renderUploadMatrix(statusMap) {
        const tbody = document.querySelector('#upload-matrix tbody');
        tbody.innerHTML = '';

        const now = new Date();
        const banks = ["BBVA Débito", "BBVA Crédito", "Scotiabank Débito", "Scotiabank Crédito", "Banorte Crédito"];
        const monthNames = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

        for (let i = 0; i < 12; i++) {
            const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
            const mLabel = `${monthNames[d.getMonth()]}-${d.getFullYear()}`;

            const tr = document.createElement('tr');
            tr.innerHTML = `<td>${mLabel.toUpperCase()}</td>`;

            banks.forEach(bank => {
                const td = document.createElement('td');
                td.className = 'matrix-cell';
                const cellData = statusMap[mLabel] && statusMap[mLabel][bank];
                const hasData = !!cellData;

                if (hasData) {
                    td.innerHTML = `
                        <div class="matrix-cell-content">
                            <div class="status-icon uploaded">✅</div>
                            <button class="matrix-delete-btn" title="Eliminar este estado de cuenta">🗑️</button>
                        </div>
                    `;

                    // Delete button handler
                    const deleteBtn = td.querySelector('.matrix-delete-btn');
                    deleteBtn.onclick = async (e) => {
                        e.stopPropagation();
                        if (!confirm(`¿Eliminar el estado de cuenta de ${bank} para ${mLabel.toUpperCase()}?`)) return;

                        try {
                            const res = await fetch(`/movements/by-month?bank=${encodeURIComponent(bank.split(' ')[0])}&month=${encodeURIComponent(mLabel)}`, {
                                method: 'DELETE'
                            });
                            const data = await res.json();
                            if (data.status === 'success') {
                                showFeedback(`Eliminados ${data.deleted_count} movimientos de ${bank} ${mLabel}`);
                                loadUploadMatrix();
                                loadUploads();
                            }
                        } catch (e) {
                            showFeedback('Error al eliminar', true);
                        }
                    };

                    // Upload new file on cell click (not on delete button)
                    td.onclick = (e) => {
                        if (e.target.classList.contains('matrix-delete-btn')) return;
                        currentParser = bank;
                        currentContext = { month: mLabel, bank: bank, cell: td };
                        bankFileInput.click();
                    };
                } else {
                    td.innerHTML = `<div class="status-icon pending">📤</div>`;
                    td.onclick = () => {
                        currentParser = bank;
                        currentContext = { month: mLabel, bank: bank, cell: td };
                        bankFileInput.click();
                    };
                }

                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        }
    }


    bankFileInput.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        const { month, bank, cell } = currentContext;
        const formData = new FormData();
        formData.append('files', file);
        formData.append('manual_parser', bank);
        formData.append('month', month);

        // Loading state
        const icon = cell.querySelector('.status-icon');
        const oldContent = icon.innerHTML;
        icon.innerHTML = '⌛';
        icon.classList.add('loading');

        try {
            const response = await fetch('/upload', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            const result = data.results[0];

            if (result.status === 'success') {
                icon.innerHTML = '✅';
                icon.className = 'status-icon uploaded';

                // Check for duplicates
                if (result.has_duplicates && result.duplicates_count > 0) {
                    showFeedback(`${result.movements_count} movimientos guardados. ${result.duplicates_count} posibles duplicados detectados.`);
                    showDuplicatesModal(result);
                } else {
                    showFeedback(`¡Éxito! ${result.movements_count} movimientos de ${bank} procesados.`);
                }

                openPreviewModal(result);
                loadUploads(); // Refresh uploads list
            } else {
                icon.innerHTML = oldContent;
                showFeedback(`Error: ${result.message}`, true);
            }
        } catch (err) {
            icon.innerHTML = oldContent;
            showFeedback('Error de conexión', true);
        } finally {
            icon.classList.remove('loading');
            bankFileInput.value = '';
        }
    });

    // Show duplicates modal for user confirmation
    function showDuplicatesModal(result) {
        const duplicates = result.duplicates;
        if (!duplicates || duplicates.length === 0) return;

        const modalHtml = `
            <div id="duplicates-modal" class="modal-overlay">
                <div class="modal-content" style="max-width: 750px;">
                    <header class="modal-header">
                        <h2>⚠️ Transacciones Duplicadas Detectadas</h2>
                        <button class="icon-btn close-dup-modal">✕</button>
                    </header>
                    <div class="modal-body">
                        <p style="margin-bottom: 0.5rem; color: var(--md-sys-color-on-surface-variant);">
                            Se detectaron <strong>${duplicates.length} transacciones</strong> que ya existen con la 
                            <strong>misma fecha, monto y descripción</strong>.
                        </p>
                        <p style="margin-bottom: 1rem; color: var(--md-sys-color-on-surface-variant); font-size: 0.9rem;">
                            Si realizaste la misma transacción varias veces el mismo día (ej: 2 pagos de $1,200 el 01-dic), 
                            márcalas para guardarlas. Si no, déjalas desmarcadas.
                        </p>
                        <div class="table-container" style="max-height: 300px; overflow-y: auto;">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Guardar</th>
                                        <th>📅 Fecha + 💰 Monto</th>
                                        <th>Descripción</th>
                                        <th>Tipo</th>
                                    </tr>
                                </thead>
                                <tbody>${duplicates.map((d, i) => `
                                    <tr>
                                        <td><input type="checkbox" class="dup-checkbox" data-index="${i}"></td>
                                        <td><strong style="color: var(--md-sys-color-primary);">${d.fecha_oper}</strong><br><span style="font-weight: 600;">${formatCurrency(d.monto)}</span></td>
                                        <td title="${d.descripcion}">${d.descripcion.substring(0, 35)}${d.descripcion.length > 35 ? '...' : ''}</td>
                                        <td>${d.tipo}</td>
                                    </tr>
                                `).join('')}</tbody>
                            </table>
                        </div>
                        <div style="margin-top: 1rem; display: flex; gap: 1rem; justify-content: flex-end;">
                            <button class="secondary-btn close-dup-modal">Ignorar todos</button>
                            <button class="primary-btn" id="confirm-dups-btn">Guardar seleccionados</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        document.body.insertAdjacentHTML('beforeend', modalHtml);

        // Event handlers
        document.querySelectorAll('.close-dup-modal').forEach(btn => {
            btn.onclick = () => document.getElementById('duplicates-modal').remove();
        });

        document.getElementById('confirm-dups-btn').onclick = async () => {
            const selected = [];
            document.querySelectorAll('.dup-checkbox:checked').forEach(cb => {
                selected.push(duplicates[parseInt(cb.dataset.index)]);
            });

            if (selected.length === 0) {
                document.getElementById('duplicates-modal').remove();
                return;
            }

            try {
                const confirmRes = await fetch('/confirm-duplicates', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        duplicates: selected,
                        account_number: result.account,
                        bank: result.bank,
                        account_type: result.account_type,
                        upload_id: result.upload_id
                    })
                });
                const confirmData = await confirmRes.json();
                if (confirmData.status === 'success') {
                    showFeedback(`${confirmData.saved_count} transacciones duplicadas guardadas.`);
                }
            } catch (e) {
                showFeedback('Error al guardar duplicados', true);
            }

            document.getElementById('duplicates-modal').remove();
            loadUploads();
        };
    }

    // Initial load
    loadUploadMatrix();
    loadUploads();

    // Load uploads history
    async function loadUploads() {
        try {
            const res = await fetch('/uploads');
            const uploads = await res.json();
            const tbody = document.querySelector('#uploads-table tbody');
            tbody.innerHTML = '';

            if (uploads.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--md-sys-color-on-surface-variant);">No hay archivos cargados</td></tr>';
                return;
            }

            uploads.forEach(u => {
                const tr = document.createElement('tr');
                const uploadDate = u.upload_date ? new Date(u.upload_date).toLocaleDateString('es-MX') : '-';
                tr.innerHTML = `
                    <td title="${u.original_filename}">${(u.original_filename || '').substring(0, 30)}${u.original_filename?.length > 30 ? '...' : ''}</td>
                    <td><span class="bank-tag">${u.bank || '-'}</span></td>
                    <td>${u.account_type || '-'}</td>
                    <td>${u.month || '-'}</td>
                    <td>${u.movement_count || 0}</td>
                    <td>${uploadDate}</td>
                    <td><button class="delete-btn" data-id="${u.id}" title="Eliminar archivo y movimientos">🗑️</button></td>
                `;
                tbody.appendChild(tr);
            });

            // Add delete handlers
            document.querySelectorAll('.delete-btn').forEach(btn => {
                btn.onclick = async () => {
                    const uploadId = btn.dataset.id;
                    if (!confirm('¿Estás seguro de eliminar este archivo y todos sus movimientos?')) return;

                    try {
                        const res = await fetch(`/uploads/${uploadId}`, { method: 'DELETE' });
                        const data = await res.json();
                        if (data.status === 'success') {
                            showFeedback(`Eliminado. ${data.deleted_movements} movimientos borrados.`);
                            loadUploads();
                            loadUploadMatrix();
                        } else {
                            showFeedback('Error al eliminar', true);
                        }
                    } catch (e) {
                        showFeedback('Error de conexión', true);
                    }
                };
            });
        } catch (e) {
            console.error('Error loading uploads', e);
        }
    }

    function showFeedback(message, isError = false) {
        const snackbar = document.getElementById('upload-feedback');
        const msgEl = document.getElementById('feedback-message');
        msgEl.textContent = message;
        snackbar.style.backgroundColor = isError ? '#ba1a1a' : '#322f37';
        snackbar.classList.remove('hidden');
        setTimeout(() => {
            snackbar.classList.add('hidden');
        }, 4000);
    }

    // Dashboard Data
    async function loadDashboard() {
        try {
            const res = await fetch('/dashboard');
            const data = await res.json();

            const income = data.totals?.['Abono'] || 0;
            const expenses = data.totals?.['Cargo'] || 0;
            const net = income - expenses;

            document.getElementById('total-income').textContent = formatCurrency(income);
            document.getElementById('total-expenses').textContent = formatCurrency(expenses);
            const netEl = document.getElementById('net-balance');
            netEl.textContent = formatCurrency(net);
            netEl.className = `monto ${net >= 0 ? 'positivo' : 'negativo'}`;

            renderCharts(data);
        } catch (e) {
            console.error('Error loading dashboard', e);
        }
    }

    // Reports Data and Filtering
    const bankFilter = document.getElementById('bank-filter');
    const monthFilter = document.getElementById('report-month-filter');
    const typeFilter = document.getElementById('type-filter');
    const descFilter = document.getElementById('desc-filter');

    async function loadReports() {
        try {
            // Load months dropdown if empty
            if (monthFilter.options.length <= 1) {
                await loadFilterMonths();
            }

            const bank = bankFilter.value;
            const month = monthFilter.value;
            const type = typeFilter.value;

            // Build query params
            const params = new URLSearchParams();
            if (bank) params.append('bank', bank);
            if (month) params.append('month', month);
            if (type) params.append('account_type', type);

            const res = await fetch(`/movements?${params.toString()}`);
            const data = await res.json();

            // Store data globally for local description filtering
            window.allMovements = data;
            filterDataLocally();
        } catch (e) {
            console.error('Error loading reports', e);
        }
    }

    async function loadFilterMonths() {
        try {
            const res = await fetch('/months');
            const months = await res.json();
            const filter = document.getElementById('report-month-filter');

            // Clear but keep "Todos"
            filter.innerHTML = '<option value="">Todos</option>';

            months.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                filter.appendChild(opt);
            });
        } catch (e) {
            console.error('Error loading month filter', e);
        }
    }

    function filterDataLocally() {
        if (!window.allMovements) {
            console.warn('No movements data loaded');
            return;
        }
        const query = descFilter.value.toLowerCase();
        const filtered = window.allMovements.filter(m =>
            (m.descripcion || '').toLowerCase().includes(query)
        );
        console.log(`Rendering ${filtered.length} movements`);
        renderTable(filtered);
    }

    // Event Listeners for filters
    [bankFilter, monthFilter, typeFilter].forEach(el => {
        el.addEventListener('change', loadReports);
    });

    descFilter.addEventListener('input', filterDataLocally);

    const exportBtn = document.getElementById('export-excel-btn');
    exportBtn.addEventListener('click', () => {
        const bank = bankFilter.value;
        const month = monthFilter.value;
        const type = typeFilter.value;

        const params = new URLSearchParams();
        if (bank) params.append('bank', bank);
        if (month) params.append('month', month);
        if (type) params.append('account_type', type);

        window.location.href = `/export/excel?${params.toString()}`;
    });

    function renderTable(data) {
        const tbody = document.querySelector('#movements-table tbody');
        tbody.innerHTML = '';
        data.forEach(m => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${m.fecha_oper}</td>
                <td>
                    <span class="bank-tag">${m.bank}</span>
                    <span class="bank-tag" style="background:var(--md-sys-color-secondary); color:white">${m.account_type || 'Desconocido'}</span>
                </td>
                <td>${m.descripcion}</td>
                <td class="${m.tipo === 'Abono' ? 'positivo' : 'negativo'}">${formatCurrency(m.monto)}</td>
                <td>${m.tipo}</td>
                <td><button class="small-btn">Detalle</button></td>
            `;
            tbody.appendChild(tr);
        });
    }

    // Recurrence Data
    async function loadRecurrence() {
        try {
            const res = await fetch('/recurrence/suggestions');
            const data = await res.json();
            const list = document.getElementById('recurrence-list');
            list.innerHTML = '';

            data.forEach(s => {
                const card = document.createElement('div');
                card.className = 'metric-card';
                card.innerHTML = `
                    <h3>${s.descripcion}</h3>
                    <p class="monto">${formatCurrency(s.monto)}</p>
                    <p>${s.month_year} meses detectados</p>
                    <button class="primary-btn" style="margin-top: 10px; width: 100%;">Confirmar como Gasto Fijo</button>
                `;
                list.appendChild(card);
            });
        } catch (e) {
            console.error('Error loading recurrence', e);
        }
    }

    // Helpers
    function formatCurrency(val) {
        return new Intl.NumberFormat('es-MX', { style: 'currency', currency: 'MXN' }).format(val);
    }

    let bankChart, evolutionChart;
    function renderCharts(data) {
        const ctxBank = document.getElementById('bankChart').getContext('2d');
        if (bankChart) bankChart.destroy();

        const labels = Object.keys(data.by_bank || {});
        const values = Object.values(data.by_bank || {});

        bankChart = new Chart(ctxBank, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: ['#4f46e5', '#10b981', '#f59e0b', '#ef4444'],
                    borderWidth: 0
                }]
            },
            options: {
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom', labels: { usePointStyle: true, padding: 20 } }
                }
            }
        });

        const ctxEvolution = document.getElementById('evolutionChart').getContext('2d');
        if (evolutionChart) evolutionChart.destroy();
        evolutionChart = new Chart(ctxEvolution, {
            type: 'line',
            data: {
                labels: ['Oct', 'Nov', 'Dic'],
                datasets: [{
                    label: 'Gastos',
                    data: [12000, 15000, 18000],
                    borderColor: '#4f46e5',
                    backgroundColor: 'rgba(79, 70, 229, 0.1)',
                    fill: true,
                    tension: 0.4
                }]
            },
            options: {
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: { beginAtZero: true, grid: { display: false } },
                    x: { grid: { display: false } }
                }
            }
        });
    }

    // Modal Helpers
    const modal = document.getElementById('preview-modal');
    const closeBtn = document.getElementById('close-modal');
    const confirmBtn = document.getElementById('confirm-modal');

    function openPreviewModal(result) {
        modal.classList.remove('hidden');

        // Validation Status
        const statusBox = document.getElementById('validation-status');
        const validation = result.metadata?.validation?.controles || {};
        const allOk = Object.values(validation).every(v => v === true);

        statusBox.className = `status-box ${allOk ? 'success' : 'error'}`;
        statusBox.innerHTML = allOk ? '✅ Validación completa: El balance coincide.' : '⚠️ Se detectaron discrepancias en la validación.';

        // Header Details
        const detailsGrid = document.getElementById('header-details');
        const header = result.metadata?.header || {};
        detailsGrid.innerHTML = `
            <div class="detail-item"><label>Banco</label><span>${result.bank}</span></div>
            <div class="detail-item"><label>Cuenta</label><span>${result.account}</span></div>
            <div class="detail-item"><label>Movimientos</label><span>${result.movements_count}</span></div>
        `;

        if (header.saldo_final !== undefined) {
            detailsGrid.innerHTML += `<div class="detail-item"><label>Saldo Final</label><span>${formatCurrency(header.saldo_final)}</span></div>`;
        }

        // Preview Table
        renderPreviewTable(result.movements);
    }

    function renderPreviewTable(movements) {
        const tbody = document.querySelector('#preview-table tbody');
        tbody.innerHTML = '';
        movements.forEach(m => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${m.fecha_oper}</td>
                <td>${m.descripcion}</td>
                <td class="${m.tipo === 'Abono' ? 'positivo' : 'negativo'}">${formatCurrency(m.monto)}</td>
                <td>${m.tipo}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    function closePreviewModal() {
        modal.classList.add('hidden');
    }

    closeBtn.addEventListener('click', closePreviewModal);
    confirmBtn.addEventListener('click', closePreviewModal);

    // Close on click outside
    window.addEventListener('click', (e) => {
        if (e.target === modal) closePreviewModal();
    });

    // Initial Load
    loadDashboard();
});
