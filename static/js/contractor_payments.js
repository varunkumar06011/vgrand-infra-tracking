// ============================================================
// Contractor Payments Module
// Tracks contractor contracts, work progress, and payment history
// ============================================================

var contractorContracts = [];
var contractorDetailContract = null;
var contractorPayments = [];
var contractorEditingContractId = null;
var cpSearchQuery = '';
var cpStatusFilter = 'open';
var cpVentureFilter = 'all';

// --- Helpers ---

function cpVentureName(ventureId) {
    if (!ventureId) return '';
    if (typeof venturesList === 'undefined') return '';
    var v = venturesList.find(function(x) { return x.id === ventureId; });
    return v ? v.name : '';
}

async function cpEnsureVentures() {
    if (typeof venturesList !== 'undefined' && venturesList.length) return;
    try {
        var list = await apiGet('/api/ventures');
        if (Array.isArray(list)) venturesList = list;
    } catch (e) {}
}

function cpFmtMoney(amount) {
    return '\u20B9' + (Number(amount) || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

function cpParseError(e) {
    var msg = e.message || String(e);
    var m = msg.match(/HTTP \d+: (.+)/);
    if (m) {
        try { var j = JSON.parse(m[1]); if (j.error) return j.error; } catch (_) {}
        return m[1];
    }
    return msg;
}

function cpProgressBar(pct) {
    var cls = pct >= 75 ? 'high' : pct >= 40 ? 'mid' : pct > 0 ? 'low' : 'zero';
    return '<div class="progress-bar-container"><div class="progress-bar-fill ' + cls + '" style="width:' + Math.min(pct, 100) + '%;">' + pct + '%</div></div>';
}

function cpRiskBadge(riskDelta) {
    var abs = Math.abs(riskDelta);
    if (abs <= 20) return '';
    if (riskDelta > 0) {
        return '<div class="cp-risk-badge cp-risk-warn">&#9888; Payment ahead of work by ' + abs + '%</div>';
    } else {
        return '<div class="cp-risk-badge cp-risk-info">&#9888; Work ahead of payment by ' + abs + '%</div>';
    }
}

function cpStatusClass(status) {
    if (status === 'completed') return 'completed';
    if (status === 'cancelled') return 'cancelled';
    return 'active';
}

// --- Panel open/close ---

function populateCPVentureFilter() {
    var sel = document.getElementById('cpVentureFilter');
    if (!sel) return;
    var current = sel.value || 'all';
    sel.innerHTML = '<option value="all">All Ventures</option>';
    if (typeof venturesList !== 'undefined') {
        venturesList.forEach(function(v) {
            var o = document.createElement('option');
            o.value = v.id;
            o.textContent = v.name;
            sel.appendChild(o);
        });
    }
    if (current !== 'all' && typeof venturesList !== 'undefined' && venturesList.some(function(v) { return v.id === current; })) {
        sel.value = current;
    } else {
        sel.value = 'all';
    }
}

function openContractorPaymentsPanel() {
    hideAllMainPanels();
    var panel = document.getElementById('contractorPaymentsPanel');
    if (panel) panel.style.display = '';
    cpSearchQuery = '';
    cpStatusFilter = 'open';
    cpVentureFilter = 'all';
    var si = document.getElementById('cpSearchInput');
    if (si) si.value = '';
    var sf = document.getElementById('cpStatusFilter');
    if (sf) sf.value = 'open';
    var vf = document.getElementById('cpVentureFilter');
    if (vf) vf.value = 'all';
    populateCPVentureFilter();
    loadContractorContracts();
    navigateTo('#/contractor-payments');
}

function closeContractorPaymentsPanel() {
    renderVentureDashboard();
    navigateTo('#/ventures');
}

// Wire up search input
document.addEventListener('DOMContentLoaded', function() {
    var si = document.getElementById('cpSearchInput');
    if (si) {
        si.addEventListener('input', function() {
            cpSearchQuery = this.value.trim();
            renderContractorCards();
        });
    }
    var sf = document.getElementById('cpStatusFilter');
    if (sf) {
        sf.addEventListener('change', function() {
            cpStatusFilter = this.value;
            renderContractorCards();
        });
    }
    var vf = document.getElementById('cpVentureFilter');
    if (vf) {
        vf.addEventListener('change', function() {
            cpVentureFilter = this.value;
            renderContractorCards();
        });
    }
});

// --- Data loading ---

async function loadContractorContracts() {
    try {
        contractorContracts = await apiGet('/api/contractor-contracts');
        renderContractorSummary();
        renderContractorCards();
    } catch (e) {
        var msg = cpParseError(e) || 'Failed to load contractor contracts';
        showToast(msg, true);
        var grid = document.getElementById('contractorCardsGrid');
        if (grid) grid.innerHTML = '<div class="att-empty" style="padding:32px 0;text-align:center;color:#c0392b;">' + escapeHtml(msg) + '</div>';
    }
}

// --- Summary banner ---

function renderContractorSummary() {
    var el = document.getElementById('contractorSummaryBar');
    if (!el) return;
    var totalContracts = contractorContracts.length;
    var totalValue = 0, totalPaid = 0, totalOutstanding = 0;
    contractorContracts.forEach(function(c) {
        if (c.status === 'cancelled') return;
        totalValue += parseFloat(c.total_amount) || 0;
        totalPaid += c.total_paid || 0;
        totalOutstanding += c.outstanding_amount || 0;
    });
    el.className = 'cp-summary-bar';
    el.innerHTML =
        '<div class="cp-summary-card"><span class="cp-summary-label">Total Contracts</span><span class="cp-summary-value">' + totalContracts + '</span></div>' +
        '<div class="cp-summary-card"><span class="cp-summary-label">Total Contract Value</span><span class="cp-summary-value">' + cpFmtMoney(totalValue) + '</span></div>' +
        '<div class="cp-summary-card"><span class="cp-summary-label">Total Paid</span><span class="cp-summary-value po-fin-paid">' + cpFmtMoney(totalPaid) + '</span></div>' +
        '<div class="cp-summary-card"><span class="cp-summary-label">Total Outstanding</span><span class="cp-summary-value po-fin-outstanding">' + cpFmtMoney(totalOutstanding) + '</span></div>';
}

// --- Contract cards ---

function renderContractorCards() {
    var grid = document.getElementById('contractorCardsGrid');
    if (!grid) return;
    // Filter by status ('open' shows active + completed; cancelled cards are hidden by default)
    var filtered = contractorContracts.filter(function(c) {
        var status = c.status || 'active';
        if (cpStatusFilter === 'all') return true;
        if (cpStatusFilter === 'open') return status === 'active' || status === 'completed';
        return status === cpStatusFilter;
    });
    // Filter by venture
    if (cpVentureFilter !== 'all') {
        filtered = filtered.filter(function(c) {
            return c.venture_id === cpVentureFilter;
        });
    }
    // Filter by search query (name or work description, case-insensitive)
    if (cpSearchQuery) {
        var q = cpSearchQuery.toLowerCase();
        filtered = filtered.filter(function(c) {
            return (c.person_name || '').toLowerCase().indexOf(q) !== -1 ||
                   (c.work_description || '').toLowerCase().indexOf(q) !== -1;
        });
    }
    if (!filtered.length) {
        if (!contractorContracts.length) {
            grid.innerHTML = '<div class="att-empty" style="padding:32px 0;text-align:center;">No contracts yet. Click "+ New Contract" to get started.</div>';
        } else {
            grid.innerHTML = '<div class="att-empty" style="padding:32px 0;text-align:center;">No contracts match the current filter.</div>';
        }
        return;
    }
    var html = '';
    filtered.forEach(function(c) {
        var statusLabel = c.status === 'active' ? 'Active' : c.status === 'completed' ? 'Completed' : 'Cancelled';
        var ventureName = cpVentureName(c.venture_id);
        html +=
            '<div class="po-card cp-contract-card" data-contract-id="' + escapeHtml(c.id) + '">' +
                '<div class="cp-card-header">' +
                    '<div>' +
                        '<div class="cp-card-title">' + escapeHtml(c.person_name) + '</div>' +
                        '<div class="cp-card-subtitle">' + escapeHtml(c.work_description) + (ventureName ? ' &nbsp;·&nbsp; ' + escapeHtml(ventureName) : '') + '</div>' +
                    '</div>' +
                    '<div class="cp-card-actions" style="display:flex;align-items:center;gap:6px;">' +
                        '<span class="cp-status ' + cpStatusClass(c.status) + '">' + statusLabel + '</span>' +
                        (c.status !== 'cancelled' && currentUserPermissions && currentUserPermissions.manageContracts ? '<button class="cp-delete-contract-btn" data-contract-id="' + escapeHtml(c.id) + '" title="Cancel contract">&#128465;</button>' : '') +
                    '</div>' +
                '</div>' +
                '<div class="cp-card-financials">' +
                    '<div class="cp-fin-cell"><span class="cp-fin-label">Total</span><span class="cp-fin-value">' + cpFmtMoney(c.total_amount) + '</span></div>' +
                    '<div class="cp-fin-cell"><span class="cp-fin-label">Paid</span><span class="cp-fin-value po-fin-paid">' + cpFmtMoney(c.total_paid) + '</span></div>' +
                    '<div class="cp-fin-cell"><span class="cp-fin-label">Outstanding</span><span class="cp-fin-value ' + (c.outstanding_amount > 0 ? 'po-fin-outstanding' : (c.overpaid_amount > 0 ? 'po-fin-outstanding' : 'po-fin-clear')) + '">' + (c.outstanding_amount > 0 ? cpFmtMoney(c.outstanding_amount) : (c.overpaid_amount > 0 ? '<span class="cp-fin-suffix">Overpaid by ' + cpFmtMoney(c.overpaid_amount) + '</span>' : '&#10003; Clear')) + '</span></div>' +
                '</div>' +
                '<div class="cp-progress-section">' +
                    '<div class="cp-progress-block">' +
                        '<div class="cp-progress-row"><span class="cp-progress-label">Work Progress</span><span class="cp-progress-detail">' + c.completed_units + '/' + c.total_units + ' ' + escapeHtml(c.unit_label) + '</span></div>' +
                        cpProgressBar(c.work_progress) +
                    '</div>' +
                    '<div class="cp-progress-block">' +
                        '<div class="cp-progress-row"><span class="cp-progress-label">Payment Progress</span><span class="cp-progress-detail">' + cpFmtMoney(c.total_paid) + ' / ' + cpFmtMoney(c.total_amount) + '</span></div>' +
                        cpProgressBar(c.payment_progress) +
                    '</div>' +
                '</div>' +
                cpRiskBadge(c.risk_delta) +
                '<div class="cp-card-footer">' +
                    '<span>Per ' + escapeHtml(c.unit_label || 'unit') + ': ' + cpFmtMoney(c.per_unit_rate) + '</span>' +
                    '<span>Remaining: ' + c.remaining_units + ' ' + escapeHtml(c.unit_label) + '</span>' +
                '</div>' +
            '</div>';
    });
    grid.innerHTML = html;

    grid.querySelectorAll('.cp-contract-card').forEach(function(card) {
        card.addEventListener('click', function() {
            openContractDetail(card.dataset.contractId);
        });
    });

    grid.querySelectorAll('.cp-delete-contract-btn').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            cancelContract(btn.dataset.contractId);
        });
    });
}

async function cancelContract(contractId) {
    var c = contractorContracts.find(function(x) { return x.id === contractId; });
    if (!c) return;
    showConfirm('Cancel Contract', 'Cancel "' + escapeHtml(c.person_name) + '"? The payment history will be preserved. This can be reversed from the Cancelled filter.', async function() {
        try {
            await apiPost('/api/contractor-contracts/' + encodeURIComponent(c.id) + '/cancel', {});
            showToast('Contract cancelled');
            await loadContractorContracts();
        } catch (e) {
            showToast(cpParseError(e) || 'Failed to cancel contract', true);
        }
    }, null, 'Cancel Contract', true);
}

// --- New Contract modal ---

async function openContractForm() {
    contractorEditingContractId = null;
    document.getElementById('contractFormTitle').textContent = 'New Contract';
    ['cpFormName', 'cpFormDesc', 'cpFormAmount', 'cpFormUnits', 'cpFormUnitLabel', 'cpFormNotes'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.value = '';
    });
    await cpEnsureVentures();
    var ventureSel = document.getElementById('cpFormVenture');
    if (ventureSel) {
        ventureSel.innerHTML = '<option value="">-- Select Venture --</option>';
        if (typeof venturesList !== 'undefined') {
            venturesList.forEach(function(v) {
                var o = document.createElement('option');
                o.value = v.id; o.textContent = v.name;
                ventureSel.appendChild(o);
            });
        }
        if (typeof currentVenture !== 'undefined' && currentVenture && currentVenture.id) {
            ventureSel.value = currentVenture.id;
        } else {
            ventureSel.value = '';
        }
    }
    document.getElementById('contractFormModal').classList.add('show');
}

function closeContractForm() {
    document.getElementById('contractFormModal').classList.remove('show');
    contractorEditingContractId = null;
}

document.addEventListener('DOMContentLoaded', function() {
    var saveBtn = document.getElementById('saveContractBtn');
    if (saveBtn) saveBtn.addEventListener('click', async function() {
        var person_name = document.getElementById('cpFormName').value.trim();
        var work_description = document.getElementById('cpFormDesc').value.trim();
        var total_amount = parseFloat(document.getElementById('cpFormAmount').value);
        var total_units = parseInt(document.getElementById('cpFormUnits').value, 10);
        var unit_label = document.getElementById('cpFormUnitLabel').value.trim() || 'units';
        var venture_id = document.getElementById('cpFormVenture') ? document.getElementById('cpFormVenture').value : '';
        var notes = document.getElementById('cpFormNotes').value.trim();

        if (!person_name) { showToast('Please enter contractor name', true); return; }
        if (!work_description) { showToast('Please enter work description', true); return; }
        if (!venture_id) { showToast('Please select a venture', true); return; }
        if (!total_amount || total_amount <= 0) { showToast('Please enter a valid total amount', true); return; }
        if (!total_units || total_units <= 0) { showToast('Please enter valid total units (> 0)', true); return; }

        try {
            await apiPost('/api/contractor-contracts', {
                person_name: person_name,
                work_description: work_description,
                total_amount: total_amount,
                total_units: total_units,
                unit_label: unit_label,
                venture_id: venture_id || null,
                notes: notes
            });
            showToast('Contract created');
            closeContractForm();
            await loadContractorContracts();
        } catch (e) {
            showToast(cpParseError(e) || 'Failed to create contract', true);
        }
    });

    var closeBtn = document.getElementById('closeContractForm');
    if (closeBtn) closeBtn.addEventListener('click', closeContractForm);
    var cancelBtn = document.getElementById('cancelContractForm');
    if (cancelBtn) cancelBtn.addEventListener('click', closeContractForm);
    var modal = document.getElementById('contractFormModal');
    if (modal) modal.addEventListener('click', function(e) {
        if (e.target === modal) closeContractForm();
    });
});

// --- Contract detail modal ---

async function openContractDetail(contractId) {
    var c = contractorContracts.find(function(x) { return x.id === contractId; });
    if (!c) return;
    contractorDetailContract = c;
    document.getElementById('contractDetailTitle').textContent = c.person_name + ' \u2014 ' + c.work_description;

    // Hide edit/cancel controls for roles without manageContracts permission
    var canManage = currentUserPermissions && currentUserPermissions.manageContracts;
    var editSection = document.querySelector('#contractDetailModal .po-section-label');
    if (editSection) editSection.style.display = canManage ? '' : 'none';
    var editFields = document.querySelectorAll('#contractDetailModal .invoice-form-row');
    editFields.forEach(function(row) {
        if (row.querySelector('#cpDetailName') || row.querySelector('#cpUpdateContractBtn')) {
            row.style.display = canManage ? '' : 'none';
        }
    });
    var cancelBtn = document.getElementById('cpCancelContractBtn');
    if (cancelBtn) cancelBtn.style.display = canManage ? '' : 'none';

    // Hide record payment section for roles without recordPayments permission
    var canRecord = currentUserPermissions && currentUserPermissions.recordPayments;
    var cpModal = document.getElementById('contractDetailModal');
    if (cpModal) {
        var cpRecordHeaders = cpModal.querySelectorAll('h4');
        cpRecordHeaders.forEach(function(h4) {
            if (h4.textContent.trim() === 'Record Payment') {
                h4.style.display = canRecord ? '' : 'none';
                var next = h4.nextElementSibling;
                if (next && next.classList.contains('invoice-form-row')) next.style.display = canRecord ? '' : 'none';
            }
        });
    }

    var summaryEl = document.getElementById('contractDetailSummary');
    if (summaryEl) {
        summaryEl.innerHTML =
            '<div class="cp-card-financials">' +
                '<div class="cp-fin-cell"><span class="cp-fin-label">Total</span><span class="cp-fin-value">' + cpFmtMoney(c.total_amount) + '</span></div>' +
                '<div class="cp-fin-cell"><span class="cp-fin-label">Paid</span><span class="cp-fin-value po-fin-paid">' + cpFmtMoney(c.total_paid) + '</span></div>' +
                '<div class="cp-fin-cell"><span class="cp-fin-label">Outstanding</span><span class="cp-fin-value ' + (c.outstanding_amount > 0 ? 'po-fin-outstanding' : (c.overpaid_amount > 0 ? 'po-fin-outstanding' : 'po-fin-clear')) + '">' + (c.outstanding_amount > 0 ? cpFmtMoney(c.outstanding_amount) : (c.overpaid_amount > 0 ? '<span class="cp-fin-suffix">Overpaid by ' + cpFmtMoney(c.overpaid_amount) + '</span>' : '&#10003; Clear')) + '</span></div>' +
            '</div>' +
            '<div class="cp-progress-section" style="margin-top:12px;">' +
                '<div class="cp-progress-block">' +
                    '<div class="cp-progress-row"><span class="cp-progress-label">Work Progress</span><span class="cp-progress-detail">' + c.completed_units + '/' + c.total_units + ' ' + escapeHtml(c.unit_label) + '</span></div>' +
                    cpProgressBar(c.work_progress) +
                '</div>' +
                '<div class="cp-progress-block">' +
                    '<div class="cp-progress-row"><span class="cp-progress-label">Payment Progress</span><span class="cp-progress-detail">' + cpFmtMoney(c.total_paid) + ' / ' + cpFmtMoney(c.total_amount) + '</span></div>' +
                    cpProgressBar(c.payment_progress) +
                '</div>' +
            '</div>' +
            cpRiskBadge(c.risk_delta);
    }

    var unitsInput = document.getElementById('cpDetailCompletedUnits');
    if (unitsInput) unitsInput.value = c.completed_units;

    var statusSel = document.getElementById('cpDetailStatus');
    if (statusSel) statusSel.value = c.status;

    var notesInput = document.getElementById('cpDetailNotes');
    if (notesInput) notesInput.value = c.notes || '';

    // Populate editable detail fields
    var nameInput = document.getElementById('cpDetailName');
    if (nameInput) nameInput.value = c.person_name || '';
    var descInput = document.getElementById('cpDetailDesc');
    if (descInput) descInput.value = c.work_description || '';
    var amountInput = document.getElementById('cpDetailAmount');
    if (amountInput) amountInput.value = c.total_amount || '';
    var totalUnitsInput = document.getElementById('cpDetailTotalUnits');
    if (totalUnitsInput) totalUnitsInput.value = c.total_units || '';
    var unitLabelInput = document.getElementById('cpDetailUnitLabel');
    if (unitLabelInput) unitLabelInput.value = c.unit_label || '';

    // Populate venture dropdown in detail
    var detailVentureSel = document.getElementById('cpDetailVenture');
    if (detailVentureSel) {
        detailVentureSel.innerHTML = '<option value="">-- Select Venture --</option>';
        if (typeof venturesList !== 'undefined') {
            venturesList.forEach(function(v) {
                var o = document.createElement('option');
                o.value = v.id; o.textContent = v.name;
                detailVentureSel.appendChild(o);
            });
        }
        detailVentureSel.value = c.venture_id || '';
    }

    var cancelContractBtn = document.getElementById('cpCancelContractBtn');
    if (cancelContractBtn) {
        cancelContractBtn.onclick = function() {
            showConfirm('Cancel Contract', 'Cancel this contract? The payment history will be preserved for audit. This action can be reversed by setting status back to Active.', async function() {
                try {
                    await apiPost('/api/contractor-contracts/' + encodeURIComponent(c.id) + '/cancel', {});
                    showToast('Contract cancelled');
                    closeContractDetail();
                    await loadContractorContracts();
                } catch (e) {
                    showToast(cpParseError(e) || 'Failed to cancel contract', true);
                }
            }, null, 'Cancel Contract', true);
        };
    }

    var updateBtn = document.getElementById('cpUpdateContractBtn');
    if (updateBtn) {
        updateBtn.onclick = async function() {
            var completed_units = parseInt(unitsInput.value, 10);
            var status = statusSel.value;
            var notes = notesInput.value.trim();
            var person_name = nameInput ? nameInput.value.trim() : '';
            var work_description = descInput ? descInput.value.trim() : '';
            var total_amount = amountInput ? parseFloat(amountInput.value) : 0;
            var total_units = totalUnitsInput ? parseInt(totalUnitsInput.value, 10) : c.total_units;
            var unit_label = unitLabelInput ? unitLabelInput.value.trim() : 'units';
            var venture_id = detailVentureSel ? detailVentureSel.value : '';
            if (isNaN(completed_units) || completed_units < 0) { showToast('Invalid completed units', true); return; }
            if (completed_units > total_units) { showToast('Completed units cannot exceed total units (' + total_units + ')', true); return; }
            if (!person_name) { showToast('Contractor name is required', true); return; }
            if (!work_description) { showToast('Work description is required', true); return; }
            if (!total_amount || total_amount <= 0) { showToast('Total amount must be greater than 0', true); return; }
            if (!total_units || total_units <= 0) { showToast('Total units must be greater than 0', true); return; }
            try {
                var r = await fetch('/api/contractor-contracts/' + encodeURIComponent(c.id), {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        completed_units: completed_units, status: status, notes: notes,
                        person_name: person_name, work_description: work_description,
                        total_amount: total_amount, total_units: total_units,
                        unit_label: unit_label, venture_id: venture_id || null
                    })
                });
                var data = await r.json().catch(function() { return {}; });
                if (!r.ok) throw new Error(data.error || 'Request failed');
                showToast('Contract updated');
                await loadContractorContracts();
                contractorDetailContract = contractorContracts.find(function(x) { return x.id === c.id; });
                openContractDetail(c.id);
            } catch (e) {
                showToast(cpParseError(e) || 'Failed to update contract', true);
            }
        };
    }

    var payBtn = document.getElementById('cpRecordPaymentBtn');
    if (payBtn) {
        if (c.status === 'cancelled') {
            payBtn.disabled = true;
            payBtn.textContent = 'Contract Cancelled';
        } else {
            payBtn.disabled = false;
            payBtn.textContent = 'Record';
        }
        payBtn.onclick = async function() {
            if (payBtn.disabled) return;
            var amount = parseFloat(document.getElementById('cpPayAmount').value);
            var payment_date = document.getElementById('cpPayDate').value;
            var method = document.getElementById('cpPayMethod').value;
            var reference = document.getElementById('cpPayRef').value.trim();
            var payNotes = document.getElementById('cpPayNotes').value.trim();
            if (!amount || amount <= 0) { showToast('Please enter a valid amount', true); return; }
            if (!payment_date) { showToast('Please select a payment date', true); return; }
            payBtn.disabled = true;
            payBtn.textContent = 'Recording...';
            try {
                await apiPost('/api/contractor-contracts/' + encodeURIComponent(c.id) + '/payments', {
                    amount: amount,
                    payment_date: payment_date,
                    method: method,
                    reference: reference,
                    notes: payNotes
                });
                showToast('Payment of ' + cpFmtMoney(amount) + ' recorded');
                document.getElementById('cpPayAmount').value = '';
                document.getElementById('cpPayRef').value = '';
                document.getElementById('cpPayNotes').value = '';
                await loadContractorContracts();
                contractorDetailContract = contractorContracts.find(function(x) { return x.id === c.id; });
                await loadContractPayments(c.id);
            } catch (e) {
                showToast(cpParseError(e) || 'Failed to record payment', true);
            } finally {
                payBtn.disabled = false;
                payBtn.textContent = 'Record';
            }
        };
    }

    var payDateInput = document.getElementById('cpPayDate');
    if (payDateInput) payDateInput.value = new Date().toISOString().split('T')[0];

    document.getElementById('contractDetailModal').classList.add('show');
    await loadContractPayments(c.id);
}

function closeContractDetail() {
    document.getElementById('contractDetailModal').classList.remove('show');
    contractorDetailContract = null;
    contractorPayments = [];
}

async function loadContractPayments(contractId) {
    try {
        contractorPayments = await apiGet('/api/contractor-contracts/' + encodeURIComponent(contractId) + '/payments');
        renderContractPayments();
    } catch (e) {
        contractorPayments = [];
        renderContractPayments();
    }
}

function renderContractPayments() {
    var el = document.getElementById('contractPaymentsList');
    if (!el) return;
    if (!contractorPayments.length) {
        el.innerHTML = '<div class="att-empty" style="padding:16px 0;">No payments recorded yet.</div>';
        return;
    }
    var html = '<table class="cp-detail-table"><thead><tr><th class="cp-date-cell">Date</th><th class="cp-amt-cell">Amount</th><th>Method</th><th>Reference</th><th>Notes</th><th>Recorded By</th><th></th></tr></thead><tbody>';
    contractorPayments.forEach(function(p) {
        html += '<tr>' +
            '<td class="cp-date-cell">' + escapeHtml(p.payment_date) + '</td>' +
            '<td class="cp-amt-cell po-fin-paid">' + cpFmtMoney(p.amount) + '</td>' +
            '<td>' + escapeHtml(p.method) + '</td>' +
            '<td>' + escapeHtml(p.reference || '\u2014') + '</td>' +
            '<td>' + escapeHtml(p.notes || '\u2014') + '</td>' +
            '<td>' + escapeHtml(p.recorded_by || '\u2014') + '</td>' +
            '<td><button class="btn-text cp-delete-pay-btn" data-pay-id="' + escapeHtml(p.id) + '" style="color:#dc2626;font-size:0.75rem;">Delete</button></td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;

    el.querySelectorAll('.cp-delete-pay-btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var payId = btn.dataset.payId;
            var reason = window.prompt('Enter reason for deleting this payment (required for audit trail):');
            if (!reason || !reason.trim()) {
                if (reason !== null) showToast('Deletion reason is required', true);
                return;
            }
            showConfirm('Delete Payment', 'Soft-delete this payment record? Reason: "' + reason.trim() + '"', async function() {
                try {
                    var r = await fetch('/api/contractor-payments/' + encodeURIComponent(payId) + '/delete', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ deletion_reason: reason.trim() })
                    });
                    var data = await r.json().catch(function() { return {}; });
                    if (!r.ok) throw new Error(data.error || 'Request failed');
                    showToast('Payment deleted (soft-delete)');
                    if (contractorDetailContract) {
                        await loadContractorContracts();
                        contractorDetailContract = contractorContracts.find(function(x) { return x.id === contractorDetailContract.id; });
                        await loadContractPayments(contractorDetailContract.id);
                        openContractDetail(contractorDetailContract.id);
                    }
                } catch (e) {
                    showToast(cpParseError(e) || 'Failed to delete payment', true);
                }
            }, null, 'Delete', true);
        });
    });
}

// --- Event wiring ---

document.addEventListener('DOMContentLoaded', function() {
    var closeDetail = document.getElementById('closeContractDetail');
    if (closeDetail) closeDetail.addEventListener('click', closeContractDetail);

    var detailModal = document.getElementById('contractDetailModal');
    if (detailModal) detailModal.addEventListener('click', function(e) {
        if (e.target === detailModal) closeContractDetail();
    });
});
