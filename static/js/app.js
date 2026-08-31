// ========================
// Utility Functions
// ========================

function togglePasswordVisibility(inputId, eyeEl) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const isHidden = input.type === 'password';
    input.type = isHidden ? 'text' : 'password';
    eyeEl.style.opacity = isHidden ? '1' : '0.5';
    eyeEl.innerHTML = isHidden ? '&#128065;&#65038;' : '&#128065;';
}

// ========================
// API Persistence
// ========================

// Short-lived in-memory cache for GET requests to prevent duplicate fetches
// when switching panels rapidly. TTL: 3 seconds (polling refreshes every 15s).
const _apiCache = new Map();
const _API_CACHE_TTL = 3000; // ms

async function apiGet(path, opts = {}) {
    // Bypass cache for cell-related endpoints (they need fresh data for color updates)
    const isCellEndpoint = path.startsWith('/api/cell') || path.startsWith('/api/cells');
    const bypassCache = opts.bypassCache || isCellEndpoint;

    if (!bypassCache) {
        const cached = _apiCache.get(path);
        if (cached && Date.now() - cached.t < _API_CACHE_TTL) {
            return cached.v;
        }
    }

    const res = await fetch(path);
    if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}: ${text}`);
    }
    const json = await res.json();
    if (!bypassCache) {
        _apiCache.set(path, { v: json, t: Date.now() });
    }
    return json;
}

async function apiPost(path, data) {
    const res = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}: ${text}`);
    }
    // Invalidate cache on mutations
    _apiCache.clear();
    return res.json();
}

async function apiUpload(path, formData) {
    const res = await fetch(path, {
        method: 'POST',
        body: formData
    });
    if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}: ${text}`);
    }
    // Invalidate cache on mutations
    _apiCache.clear();
    return res.json();
}

async function apiDelete(path) {
    const res = await fetch(path, { method: 'DELETE' });
    if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}: ${text}`);
    }
    // Invalidate cache on mutations
    _apiCache.clear();
    return res.json().catch(() => ({}));
}

function generateId() {
    return 'id_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}

// ========================
// App State
// ========================
let currentUser = null;
let currentUserRole = null;
let currentUserPermissions = {};

// ========================
// Role-Based Sidebar Configuration
// ========================
const SIDEBAR_CONFIG = {
    admin: {
        label: 'Builder Admin',
        sections: [
            {
                title: 'Overview',
                items: [
                    { id: 'sidebarOverview', icon: '\u{1F3E0}', label: 'Overview' },
                ]
            },
            {
                title: 'Construction',
                items: [
                    { id: 'sidebarDashboard', icon: '\u{1F3D7}\uFE0F', label: 'Dashboard', action: 'tracker' },
                    { id: 'openInventoryBtn', icon: '\u{1F4E6}', label: 'Inventory' },
                    { id: 'openVentureAnalysisBtn', icon: '\u{1F4CA}', label: 'Venture Analysis' },
                    { id: 'openDayBookBtn', icon: '\u{1F9FE}', label: 'Day Book' },
                ]
            },
            {
                title: 'Financials',
                items: [
                    { id: 'openAttendanceBtn', icon: '\u{1F465}', label: 'Attendance' },
                    { id: 'openVendorsBtn', icon: '\u{1F465}', label: 'Vendors' },
                    { id: 'openContractorPaymentsBtn', icon: '\u{1F527}', label: 'Contractor Payments' },
                ]
            },
            {
                title: 'Reports',
                items: [
                    { id: 'openInstantReportsBtn', icon: '\u26A0\uFE0F', label: 'Instant Reports' },
                ]
            },
            {
                title: 'RWA',
                items: [
                    { id: 'openRWABtn', icon: '\u{1F3D8}\uFE0F', label: 'RWA Overview', href: '/rwa-admin' },
                ]
            },
            {
                title: 'Compliance',
                items: [
                    { id: 'openRERABtn', icon: '\u{1F4CA}', label: 'RERA QPR', href: '/rera' },
                    { id: 'openLenderReportBtn', icon: '\u{1F3E2}', label: 'Lender Report' },
                ]
            },
            {
                title: 'Settings',
                items: [
                    { id: 'editModeBtn', icon: '\u270F\uFE0F', label: 'Edit Structure' },
                    { id: 'settingsBtn', icon: '\u2699\uFE0F', label: 'Settings' },
                    { id: 'manageUsersBtn', icon: '\u{1F465}', label: 'Manage Users' },
                ]
            },
        ]
    },

    manager: {
        label: 'Project Manager',
        sections: [
            {
                title: 'Overview',
                items: [
                    { id: 'sidebarOverview', icon: '\u{1F3E0}', label: 'Overview' },
                ]
            },
            {
                title: 'Construction',
                items: [
                    { id: 'sidebarDashboard', icon: '\u{1F3D7}\uFE0F', label: 'Dashboard', action: 'tracker' },
                    { id: 'openInventoryBtn', icon: '\u{1F4E6}', label: 'Inventory' },
                    { id: 'openVentureAnalysisBtn', icon: '\u{1F4CA}', label: 'Venture Analysis' },
                    { id: 'openDayBookBtn', icon: '\u{1F9FE}', label: 'Day Book' },
                ]
            },
            {
                title: 'Financials',
                items: [
                    { id: 'openAttendanceBtn', icon: '\u{1F465}', label: 'Attendance' },
                    { id: 'openVendorsBtn', icon: '\u{1F465}', label: 'Vendors' },
                ]
            },
            {
                title: 'Reports',
                items: [
                    { id: 'openInstantReportsBtn', icon: '\u26A0\uFE0F', label: 'Instant Reports' },
                ]
            },
            {
                title: 'RWA',
                items: [
                    { id: 'openRWABtn', icon: '\u{1F3D8}\uFE0F', label: 'RWA Overview', href: '/rwa-admin' },
                ]
            },
            {
                title: 'Compliance',
                items: [
                    { id: 'openRERABtn', icon: '\u{1F4CA}', label: 'RERA QPR', href: '/rera' },
                    { id: 'openLenderReportBtn', icon: '\u{1F3E2}', label: 'Lender Report' },
                    { id: 'openDesignGeneratorBtn', icon: '\u{1F3A8}', label: 'Design Generator' },
                ]
            },
            {
                title: 'Settings',
                items: [
                    { id: 'editModeBtn', icon: '\u270F\uFE0F', label: 'Edit Structure' },
                    { id: 'settingsBtn', icon: '\u2699\uFE0F', label: 'Settings' },
                ]
            },
        ]
    },

    supervisor: {
        label: 'Site Supervisor',
        sections: [
            {
                title: 'Overview',
                items: [
                    { id: 'sidebarOverview', icon: '\u{1F3E0}', label: 'Overview' },
                ]
            },
            {
                title: 'Construction',
                items: [
                    { id: 'sidebarDashboard', icon: '\u{1F3D7}\uFE0F', label: 'Dashboard', action: 'tracker' },
                    { id: 'openInventoryBtn', icon: '\u{1F4E6}', label: 'Inventory' },
                    { id: 'openVentureAnalysisBtn', icon: '\u{1F4CA}', label: 'Venture Analysis' },
                    { id: 'openDayBookBtn', icon: '\u{1F9FE}', label: 'Day Book' },
                ]
            },
            {
                title: 'Financials',
                items: [
                    { id: 'openAttendanceBtn', icon: '\u{1F465}', label: 'Attendance' },
                    { id: 'openVendorsBtn', icon: '\u{1F465}', label: 'Vendor Payments' },
                    { id: 'openContractorPaymentsBtn', icon: '\u{1F527}', label: 'Contractor Payments' },
                ]
            },
            {
                title: 'Reports',
                items: [
                    { id: 'openReportsBtn', icon: '\u{1F4C8}', label: 'Reports' },
                    { id: 'openInstantReportsBtn', icon: '\u26A0\uFE0F', label: 'Instant Reports' },
                ]
            },
        ]
    },

    security: {
        label: 'Security',
        sections: [
            {
                title: 'Gate',
                items: [
                    { id: 'navVisitorLog', icon: '\u{1F6AA}', label: 'Visitor Log', href: '/visitor-portal#visitors' },
                    { id: 'navDeliveries', icon: '\u{1F4E6}', label: 'Deliveries', href: '/visitor-portal#deliveries' },
                ]
            },
            {
                title: 'Patrol',
                items: [
                    { id: 'navPatrol', icon: '\u{1F6E1}\uFE0F', label: 'Patrol', href: '/visitor-portal#patrol' },
                ]
            },
            {
                title: 'Alerts',
                items: [
                    { id: 'navSOS', icon: '\u{1F6A8}', label: 'SOS Alerts', href: '/visitor-portal#sos' },
                ]
            },
        ]
    },

    resident: {
        label: 'Resident',
        sections: [
            {
                title: 'My Home',
                items: [
                    { id: 'navMyRequests', icon: '\u{1F4CB}', label: 'My Requests', href: '/visitor-portal#my-requests' },
                    { id: 'navComplaints', icon: '\u{1F527}', label: 'Complaints', href: '/visitor-portal#complaints' },
                ]
            },
            {
                title: 'Community',
                items: [
                    { id: 'navNotices', icon: '\u{1F4E2}', label: 'Notices', href: '/visitor-portal#notices' },
                    { id: 'navAmenities', icon: '\u{1F3CA}', label: 'Amenities', href: '/visitor-portal#amenities' },
                ]
            },
            {
                title: 'Financials',
                items: [
                    { id: 'navLedger', icon: '\u{1F4D0}', label: 'Ledger', href: '/visitor-portal#ledger' },
                ]
            },
        ]
    },

    society_head: {
        label: 'Society Head',
        sections: [
            {
                title: 'Community',
                items: [
                    { id: 'navDirectory', icon: '\u{1F4C7}', label: 'Directory', href: '/visitor-portal#directory' },
                    { id: 'navComplaintsAll', icon: '\u{1F527}', label: 'Complaints (All)', href: '/visitor-portal#complaints' },
                    { id: 'navNoticesHead', icon: '\u{1F4E2}', label: 'Notices', href: '/visitor-portal#notices' },
                ]
            },
            {
                title: 'Operations',
                items: [
                    { id: 'navVendorLedger', icon: '\u{1F4D0}', label: 'Vendor Ledger', href: '/visitor-portal#vendor-ledger' },
                    { id: 'navVisitorPolicy', icon: '\u{1F6AA}', label: 'Visitor Policy', href: '/visitor-portal#visitor-policy' },
                    { id: 'navDefectLog', icon: '\u{1F41B}', label: 'Defect Log', href: '/visitor-portal#defect-log' },
                ]
            },
        ]
    },
};

let activeNavId = null;
let currentVenture = null;
let currentBlockObj = null;
let currentBlock = 'A';
let currentFloor = 1;
let workItems = [];
let cellsCache = {};
const CELL_NOT_FOUND = Object.freeze({ __notFound: true });
const pendingSaves = new Map(); // cellKey -> debounce timeout
let inFlightSaves = 0; // count of API save calls currently in-flight
let bulkMode = false;
let bulkSelectedColor = null; // when set, clicking a cell instantly applies this color (paint mode)
let bulkIsDragging = false; // when true, mouse drag applies paint color to cells
const bulkSelected = new Set(); // set of cacheKeys selected in bulk mode
const bulkPendingChanges = new Map(); // paint mode: ck -> {oldData, newData} for deferred save
const bulkOriginalData = new Map(); // paint mode: ck -> original cellsCache value for cancel/revert
let selectedCellId = null;
let selectedWorkItem = null;
let selectedFlat = null;
let venturesList = [];
let remarksImagesBuffer = [];

// Global caches for invoices, POs, vendors, categories (lazy-loaded when panel opens)
let allInvoices = [];
let allCategories = [];
let allPOs = [];
let allVendors = [];
let _invoicesLoaded = false;
let _posLoaded = false;
let _vendorsLoaded = false;
let _categoriesLoaded = false;

const DEFAULT_INVOICE_CATEGORIES = [
    'Brick', 'Sand', 'Steel', 'Cement', 'Tiles',
    'Electrical', 'Plumbing', 'Labour', 'Paint', 'Wood'
];

async function ensureInvoicesLoaded() {
    if (_invoicesLoaded) return;
    _invoicesLoaded = true;
    try {
        allInvoices = await apiGet('/api/invoices') || [];
    } catch (e) {
        allInvoices = [];
        _invoicesLoaded = false;
    }
}

async function ensurePOsLoaded() {
    if (_posLoaded) return;
    _posLoaded = true;
    try {
        allPOs = await apiGet('/api/pos') || [];
    } catch (e) {
        allPOs = [];
        _posLoaded = false;
    }
}

async function ensureVendorsLoaded() {
    if (_vendorsLoaded) return;
    _vendorsLoaded = true;
    try {
        allVendors = await apiGet('/api/vendors') || [];
    } catch (e) {
        allVendors = [];
        _vendorsLoaded = false;
    }
}

async function ensureCategoriesLoaded() {
    if (_categoriesLoaded) return;
    _categoriesLoaded = true;
    try {
        allCategories = await apiGet('/api/settings/invoice_categories') || DEFAULT_INVOICE_CATEGORIES;
    } catch (e) {
        allCategories = DEFAULT_INVOICE_CATEGORIES;
        _categoriesLoaded = false;
    }
}

const DEFAULT_WORK_ITEMS = [
    "BRICK WORK", "ELECTRICAL PIPES", "MESH", "PLASTERING",
    "CEILING PAINT", "POP FRAME", "CEILING WIRING", "POP SHEETS",
    "WALL CARE", "BATHROOM PLUMBING", "WINDOW FRAME", "BATH SWR LINES",
    "BATH CONCEALING", "TILES", "DOORS FITTING", "PAINT PRIMER",
    "PAINT 1st COAT", "WINDOWS PAINT", "SWITCH BOARD FITTING",
    "PATCH WORK", "2nd COAT PAINTING"
];

const COLOR_LABELS = {
    red: 'Yet to start',
    yellow: 'In progress',
    blue: 'Patch work',
    green: 'Completed'
};

const FLATS_PER_FLOOR = 6;

const WORK_CATEGORIES = {
    'CIVIL WORK': [
        "Brick work", "Lintel", "Lanter", "Mesh", "Mesh & Brickwork NCC",
        "Connections", "Lift", "Cupboards", "Red Oxide Duraplus Primer",
        "Red Oxide Duraplus Primer (2nd coat)", "Bathroom Service Chargable"
    ],
    'ELECTRICAL & PLUMBING WORK': [
        "Electrical pipe", "Pipe & GI box", "Wiring",
        "Bathroom Chipped", "Bathroom Geyser Pipe",
        "Bathroom Geyser & Pipes", "Sanitary Board & Nand",
        "GC & Bath Fitting"
    ],
    'POP CEILING': [
        "Pop bolster work", "Pop ready work", "Casing",
        "Balloon PVC Box Fitting", "Connections / Measurement"
    ],
    'PAINTING': [
        "Colour Primer", "Wall Care Plaster",
        "Wall Care Slastoat", "Wall Primer", "Primer",
        "Colour to Edge"
    ],
    'FLOORING': [
        "Bathroom Wall Tiles", "Tile Laying",
        "Tile Cutting", "Connections", "Window Dhanis",
        "Colour to Edge", "Wedding Dhanis"
    ],
    'CORRIDORS': [
        { id: 'corridor_0', label: 'Plaster' },
        { id: 'corridor_1', label: 'Mesh' },
        { id: 'corridor_2', label: 'Lanter' },
        { id: 'corridor_3', label: 'Wiring' },
        { id: 'corridor_4', label: 'Stains & Cleaning' },
        { id: 'corridor_5', label: 'Flooring' }
    ],
    'ELEVATION WORK': [
        { id: 'elevation_0', label: 'Marka' },
        { id: 'elevation_1', label: 'Elevation' },
        { id: 'elevation_2', label: 'Electrics' },
        { id: 'elevation_3', label: 'Wall Care' },
        { id: 'elevation_4', label: 'Texture' }
    ]
};

// Special categories that render against a single P-004 flat instead of regular flat numbers
const CATEGORY_FLATS = {
    'CORRIDORS': ['P-004'],
    'ELEVATION WORK': ['P-004']
};

const SUPER_STRUCTURE_ITEMS = [
    "Site Preparation", "Excavation", "Marking", "Piles", "Piles Concrete",
    "Pile Caps", "Plinth Beam", "Plinth Wall", "Filling", "40mm Bed",
    "Sunken Tank", "Columns for 1st Slab", "Slab Shuttering for 1st Slab", "Bar Bending for 1st Slab", "Electrical Pipes",
    "1st Slab Casting", "Columns for 2nd Slab", "Shuttering for 2nd Slab", "Bar Bending for 2nd Slab", "Electrical Pipes",
    "2nd Slab Casting", "Columns for 3rd Slab", "Slab Shuttering for 3rd Slab", "Bar Bending for 3rd Slab", "Electrical Pipes",
    "3rd Slab Casting", "Columns for 4th Slab", "Slab Shuttering for 4th Slab", "Bar Bending for 4th Slab", "Electrical Pipes",
    "4th Slab Casting", "Columns for 5th Slab", "Slab Shuttering for 5th Slab", "Bar Bending for 5th Slab", "Electrical Pipes",
    "5th Slab Casting", "Columns for 6th Slab", "Slab Shuttering for 6th Slab", "Bar Bending for 6th Slab", "Electrical Pipes",
    "6th Slab Casting", "Columns for Lift Tank & Stairs", "Shuttering for Above", "Slab Casting", "Water Tank Bar Bending",
    "Water Tank NCC", "Elevation Scaffolding", "Elevation Mess & Packing", "Elevation Brick Work", "Elevation (Plastering)",
    "Electrical SWM & Plumbing Outside Lines", "1M CH Work", "Scaffolding Removal", "Patch Work", "Elevation Texture",
    "Elevation Primer", "Elevation Paint 1st Coat", "Compound Wall Columns & Beam", "Compound Wall Brick & Plastering",
    "Compound Wall Paint", "Final Coat"
];

let currentView = 'work';
let editMode = false;
let archivedItems = {};
let pendingFilterFloor = 'all';
let pendingFilterFlat = 'all';
let lastPendingRows = [];
let homeQuickReportType = 'pending';
let homeQuickReportVenture = null;
let homeQuickReportBlock = null;
let homeQuickReportFloor = 1;
let homeQuickReportFlat = 'all';

// ========================
// URL Router & State Persistence
// ========================
const APP_STATE_KEY = 'penguin_os_state';

function getElValue(id) {
    const el = document.getElementById(id);
    return el ? el.value : '';
}

function setElValue(id, value) {
    const el = document.getElementById(id);
    if (el && value !== undefined && value !== null) el.value = value;
}

function buildPanelState() {
    return {
        invoices: {
            venture: getElValue('invoiceFilterVenture'),
            category: getElValue('invoiceFilterCategory'),
            from: getElValue('invoiceFilterFrom'),
            to: getElValue('invoiceFilterTo')
        },
        po: {
            status: getElValue('poFilterStatus'),
            venture: getElValue('poFilterVenture'),
            vendor: getElValue('poFilterVendor'),
            type: getElValue('poFilterType'),
            from: getElValue('poFilterFrom'),
            to: getElValue('poFilterTo')
        },
        payroll: {
            selectedVentureId: selectedAttendanceVenture ? selectedAttendanceVenture.id : null
        },
        inventory: {
            selectedVentureId: selectedInventoryVenture ? selectedInventoryVenture.id : null,
            tab: inventoryTab,
            regType: inventoryRegTypeFilter,
            regMaterial: inventoryRegMaterialFilter,
            locMaterial: inventoryLocMaterialFilter,
            locBlock: inventoryLocBlockFilter,
            locFloor: inventoryLocFloorFilter,
            vendor: inventoryVendorFilter,
            vendorMaterial: inventoryVendorMaterialFilter
        },
        expenditure: {
            selectedVentureId: selectedExpenditureVenture ? selectedExpenditureVenture.id : null,
            from: expenditureFromDate,
            to: expenditureToDate,
            tab: expenditureActiveTab
        }
    };
}

function saveAppState() {
    const state = {
        hash: window.location.hash,
        panelState: buildPanelState()
    };
    try {
        localStorage.setItem(APP_STATE_KEY, JSON.stringify(state));
    } catch (e) {}
}

function loadAppState() {
    try {
        return JSON.parse(localStorage.getItem(APP_STATE_KEY) || '{}');
    } catch (e) {
        return {};
    }
}

function restorePanelState(panel) {
    const state = loadAppState().panelState || {};
    const p = state[panel];
    if (!p) return;
    if (panel === 'invoices') {
        setElValue('invoiceFilterVenture', p.venture);
        setElValue('invoiceFilterCategory', p.category);
        setElValue('invoiceFilterFrom', p.from);
        setElValue('invoiceFilterTo', p.to);
    } else if (panel === 'po') {
        setElValue('poFilterStatus', p.status);
        setElValue('poFilterVenture', p.venture);
        setElValue('poFilterVendor', p.vendor);
        setElValue('poFilterType', p.type);
        setElValue('poFilterFrom', p.from);
        setElValue('poFilterTo', p.to);
    } else if (panel === 'payroll') {
        if (p.selectedVentureId) {
            selectedAttendanceVenture = venturesList.find(v => v.id === p.selectedVentureId) || null;
        }
    } else if (panel === 'inventory') {
        if (p.selectedVentureId) {
            selectedInventoryVenture = venturesList.find(v => v.id === p.selectedVentureId) || null;
        }
        if (p.tab) inventoryTab = p.tab;
        inventoryRegTypeFilter = p.regType || 'all';
        inventoryRegMaterialFilter = p.regMaterial || 'all';
        inventoryLocMaterialFilter = p.locMaterial || 'all';
        inventoryLocBlockFilter = p.locBlock || 'all';
        inventoryLocFloorFilter = p.locFloor || 'all';
        inventoryVendorFilter = p.vendor || 'all';
        inventoryVendorMaterialFilter = p.vendorMaterial || 'all';
    } else if (panel === 'expenditure') {
        if (p.selectedVentureId) {
            selectedExpenditureVenture = venturesList.find(v => v.id === p.selectedVentureId) || null;
        }
        expenditureFromDate = p.from || '';
        expenditureToDate = p.to || '';
        expenditureActiveTab = p.tab || 'supervisor';
    }
}

function buildTrackerRoute() {
    if (!currentVenture) return '#/ventures';
    const block = currentBlock || 'A';
    const floor = currentFloor || 1;
    const view = ['work', 'super'].includes(currentView) ? currentView : 'work';
    return `#/venture/${encodeURIComponent(currentVenture.id)}/${block}/${floor}/${view}`;
}

let ignoreNextHashChange = false;

function navigateTo(hash) {
    const target = hash.startsWith('#') ? hash : '#' + hash;
    // Close all open modals when navigating away
    document.querySelectorAll('.modal.show').forEach(m => m.classList.remove('show'));
    if (window.location.hash !== target) {
        ignoreNextHashChange = true;
        window.location.hash = target;
    }
    saveAppState();
}

function parseHash(hash) {
    const h = (hash || window.location.hash).replace(/^#/, '');
    if (!h) return { route: 'ventures' };
    const parts = h.split('/').filter(Boolean);
    if (parts[0] === 'ventures') return { route: 'ventures' };
    if (parts[0] === 'overview') return { route: 'overview' };
    if (parts[0] === 'invoices') return { route: 'invoices' };
    if (parts[0] === 'pos') return { route: 'pos' };
    if (parts[0] === 'payroll') return { route: 'payroll' };
    if (parts[0] === 'inventory') return { route: 'inventory' };
    if (parts[0] === 'day-book') return { route: 'day-book' };
    if (parts[0] === 'vendors') return { route: 'vendors' };
    if (parts[0] === 'expenditure') return { route: 'expenditure' };
    if (parts[0] === 'contractor-payments') return { route: 'contractor-payments' };
    if (parts[0] === 'reports') return { route: 'reports' };
    if (parts[0] === 'instant-reports') return { route: 'instant-reports' };
    if (parts[0] === 'inventory-audit') return { route: 'inventory-audit' };
    if (parts[0] === 'design-generator') return { route: 'design-generator' };
    if (parts[0] === 'venture-analysis') return { route: 'venture-analysis' };
    if (parts[0] === 'venture' && parts[1]) {
        return { route: 'tracker', ventureId: parts[1], block: parts[2], floor: parts[3], view: parts[4] };
    }
    return { route: 'ventures' };
}

async function applyHashRoute() {
    const saved = loadAppState();
    let hash = window.location.hash;
    // Restore the last visited section if the URL has no hash
    if (!hash && saved.hash) {
        hash = saved.hash;
        ignoreNextHashChange = true;
        window.location.hash = saved.hash;
    } else if (!hash && currentUserRole === 'admin') {
        // First visit with no saved state: default admin to overview
        hash = '#/overview';
        ignoreNextHashChange = true;
        window.location.hash = '#/overview';
    }
    const route = parseHash(hash);

    if (route.route === 'ventures') {
        exitToDashboard();
    } else if (route.route === 'overview') {
        if (typeof renderOverviewPage === 'function') renderOverviewPage();
    } else if (route.route === 'tracker') {
        const venture = venturesList.find(v => v.id === route.ventureId);
        if (venture) {
            await openVenture(venture, {
                block: route.block,
                floor: route.floor ? parseInt(route.floor) : undefined,
                view: route.view
            });
        } else {
            exitToDashboard();
        }
    } else if (route.route === 'invoices') {
        openInvoicesPanel();
    } else if (route.route === 'pos') {
        openPOPanel();
    } else if (route.route === 'payroll') {
        openPayrollPanel();
    } else if (route.route === 'attendance') {
        openAttendancePanel();
    } else if (route.route === 'inventory') {
        if (typeof openInventoryRegisterPanel === 'function') openInventoryRegisterPanel();
        else openInventoryPanel();
    } else if (route.route === 'venture-analysis') {
        if (typeof openVentureAnalysisPanel === 'function') openVentureAnalysisPanel();
    } else if (route.route === 'day-book') {
        if (typeof openDayBookPanel === 'function') openDayBookPanel();
    } else if (route.route === 'vendors') {
        if (typeof openVendorDirPanel === 'function') openVendorDirPanel();
    } else if (route.route === 'expenditure') {
        openExpenditurePanel();
    } else if (route.route === 'contractor-payments') {
        openContractorPaymentsPanel();
    } else if (route.route === 'reports') {
        openReportsPanel();
    } else if (route.route === 'instant-reports') {
        openInstantReportsPanel();
    } else if (route.route === 'inventory-audit') {
        openInventoryAuditPanel();
    } else if (route.route === 'design-generator') {
        openDesignGeneratorPanel();
    }
    restorePanelState(route.route);
}

window.addEventListener('hashchange', () => {
    if (ignoreNextHashChange) {
        ignoreNextHashChange = false;
        return;
    }
    applyHashRoute();
});
window.addEventListener('beforeunload', saveAppState);
window.addEventListener('beforeunload', flushPendingSaves);

function flushPendingSaves() {
    if (pendingSaves.size === 0) return;
    // Fire each pending save immediately via sendBeacon (fire-and-forget during page teardown)
    pendingSaves.forEach((timer, ck) => {
        clearTimeout(timer);
        const cellData = cellsCache[ck];
        if (cellData && !cellData.__notFound) {
            try {
                const blob = new Blob([JSON.stringify(cellData)], { type: 'application/json' });
                navigator.sendBeacon('/api/cell/' + encodeURIComponent(ck), blob);
            } catch (e) {}
        }
    });
    pendingSaves.clear();
}

function cacheKey(cellId) {
    return currentVenture ? `${currentVenture.id}_${cellId}` : cellId;
}

function createImageIndicator(count) {
    if (!count) return null;
    const badge = document.createElement('span');
    badge.className = 'remarks-image-indicator';
    badge.textContent = count > 9 ? '9+' : count;
    badge.title = `${count} photo${count > 1 ? 's' : ''}`;
    return badge;
}

function compressImage(file, maxWidth = 1920, maxHeight = 1920, quality = 0.8) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        const url = URL.createObjectURL(file);
        img.onload = () => {
            URL.revokeObjectURL(url);
            let { width, height } = img;
            if (width > maxWidth || height > maxHeight) {
                const ratio = Math.min(maxWidth / width, maxHeight / height);
                width = Math.round(width * ratio);
                height = Math.round(height * ratio);
            }
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#fff';
            ctx.fillRect(0, 0, width, height);
            ctx.drawImage(img, 0, 0, width, height);
            const dataUrl = canvas.toDataURL('image/jpeg', quality);
            resolve({
                name: file.name.replace(/\.[^.]+$/, '.jpg'),
                type: 'image/jpeg',
                dataUrl,
                size: Math.round(dataUrl.length * 0.75)
            });
        };
        img.onerror = () => {
            URL.revokeObjectURL(url);
            reject(new Error('Failed to load image'));
        };
        img.src = url;
    });
}

function slugId(text) {
    return text.toLowerCase().replace(/[^a-z0-9]/g, '_').substring(0, 30);
}

function ensureItemIds(items) {
    if (!items || !items.length) return [];
    return items.map((item) => {
        if (typeof item === 'object' && item.id) return item;
        const label = typeof item === 'string' ? item : (item && item.label) || 'Untitled';
        return { id: `item_${slugId(label)}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`, label };
    });
}

function getWorkCategoryDisplayName(cat) {
    const map = {
        'CIVIL WORK': 'Civil Work',
        'ELECTRICAL & PLUMBING WORK': 'Electrical & Plumbing',
        'POP CEILING': 'Ceiling',
        'PAINTING': 'Painting',
        'FLOORING': 'Flooring',
        'CORRIDORS': 'Corridors',
        'ELEVATION WORK': 'Elevation Work'
    };
    return map[cat] || cat;
}

function sortWorkCategoryNames(names) {
    // Check userPrefs.workCategoryOrder first (per-user drag-reorder)
    if (userPrefs && userPrefs.workCategoryOrder && userPrefs.workCategoryOrder.length > 0) {
        const userOrder = userPrefs.workCategoryOrder;
        const remaining = names.filter(n => !userOrder.includes(n));
        const sorted = [];
        userOrder.forEach(key => {
            if (names.includes(key)) sorted.push(key);
        });
        return sorted.concat(remaining);
    }
    // Fall back to default hardcoded order
    const order = ['CIVIL WORK', 'ELECTRICAL & PLUMBING WORK', 'PAINTING', 'POP CEILING', 'FLOORING', 'CORRIDORS', 'ELEVATION WORK'];
    const remaining = names.filter(n => !order.includes(n));
    const sorted = [];
    order.forEach(key => {
        if (names.includes(key)) sorted.push(key);
    });
    return sorted.concat(remaining);
}

function ensureWorkCategories(cats) {
    if (!cats || Object.keys(cats).length === 0) return JSON.parse(JSON.stringify(WORK_CATEGORIES));
    const result = {}
    Object.entries(cats).forEach(([catLabel, items]) => {
        // Defensive: ensure items is an array
        if (!Array.isArray(items)) {
            result[catLabel] = [];
            return;
        }
        // Use existing items if they already have IDs; otherwise generate IDs
        if (items.length > 0 && typeof items[0] === 'object' && items[0].id) {
            result[catLabel] = items;
        } else {
            result[catLabel] = items.map((label, i) => ({ id: `item_${slugId(catLabel)}_${slugId(label)}_${i}`, label }));
        }
    });
    return result;
}

function getFlatWorkItems() {
    return [];
}

function getSuperStructureItems() {
    if (currentVenture && currentVenture.super_structure_items && currentVenture.super_structure_items.length > 0) {
        return ensureItemIds(currentVenture.super_structure_items);
    }
    return ensureItemIds(SUPER_STRUCTURE_ITEMS);
}

function cellKeyById(block, floor, flat, itemId) {
    return `${block}_floor${floor}_${flat}_${itemId}`;
}

function ssCellKeyById(itemId) {
    return `superstructure_${currentBlock}_${itemId}`;
}

// ========================
// DOM Elements
// ========================
const els = {
    userEmail: document.getElementById('userEmail'),
    signOutBtn: document.getElementById('signOutBtn'),
    settingsBtn: document.getElementById('settingsBtn'),
    statusPopup: document.getElementById('statusPopup'),
    popupTitle: document.getElementById('popupTitle'),
    popupCurrentStatus: document.getElementById('popupCurrentStatus'),
    clearStatusBtn: document.getElementById('clearStatusBtn'),
    cellUsageSection: document.getElementById('cellUsageSection'),
    cellUsageList: document.getElementById('cellUsageList'),
    usageMaterialSelect: document.getElementById('usageMaterialSelect'),
    usageQtyInput: document.getElementById('usageQtyInput'),
    usageWasteInput: document.getElementById('usageWasteInput'),
    usageReasonInput: document.getElementById('usageReasonInput'),
    logUsageBtn: document.getElementById('logUsageBtn'),
    usageMsg: document.getElementById('usageMsg'),
    cancelStatusBtn: document.getElementById('cancelStatusBtn'),
    timelineModal: document.getElementById('timelineModal'),
    timelineTitle: document.getElementById('timelineTitle'),
    timelineList: document.getElementById('timelineList'),
    remarksTextarea: document.getElementById('remarksTextarea'),
    saveRemarksBtn: document.getElementById('saveRemarksBtn'),
    closeTimeline: document.getElementById('closeTimeline'),
    remarksFileDrop: document.getElementById('remarksFileDrop'),
    remarksFileInput: document.getElementById('remarksFileInput'),
    remarksFileDropLabel: document.getElementById('remarksFileDropLabel'),
    remarksFilePreview: document.getElementById('remarksFilePreview'),
    settingsModal: document.getElementById('settingsModal'),
    saveSettingsBtn: document.getElementById('saveSettingsBtn'),
    closeSettings: document.getElementById('closeSettings'),
    blocksSettingsList: document.getElementById('blocksSettingsList'),
    addBlockBtn: document.getElementById('addBlockBtn'),
    applyChangesModal: document.getElementById('applyChangesModal'),
    closeApplyChanges: document.getElementById('closeApplyChanges'),
    applyChangesCancel: document.getElementById('applyChangesCancel'),
    applyChangesConfirm: document.getElementById('applyChangesConfirm'),
    applyChangesMsg: document.getElementById('applyChangesMsg'),
    applyVentureSelect: document.getElementById('applyVentureSelect'),
    applyVentureSearch: document.getElementById('applyVentureSearch'),
    applyAllWarning: document.getElementById('applyAllWarning'),
    manageUsersBtn: document.getElementById('manageUsersBtn'),
    manageUsersModal: document.getElementById('manageUsersModal'),
    closeManageUsers: document.getElementById('closeManageUsers'),
    manageUsersCancel: document.getElementById('manageUsersCancel'),
    manageUsersSave: document.getElementById('manageUsersSave'),
    manageUsersPassword: document.getElementById('manageUsersPassword'),
    manageUsersConfirmPassword: document.getElementById('manageUsersConfirmPassword'),
    manageUsersMsg: document.getElementById('manageUsersMsg'),
    userListContainer: document.getElementById('userListContainer'),
    newUserEmail: document.getElementById('newUserEmail'),
    newUserFullName: document.getElementById('newUserFullName'),
    newUserPassword: document.getElementById('newUserPassword'),
    newUserRole: document.getElementById('newUserRole'),
    userFormTitle: document.getElementById('userFormTitle'),
    ventureAssignmentSection: document.getElementById('ventureAssignmentSection'),
    ventureCheckboxList: document.getElementById('ventureCheckboxList'),
    changePasswordSection: document.getElementById('changePasswordSection'),
};

// ========================
// Session & Auth
// ========================
function buildPermissions(role) {
    const p = {};
    if (role === 'supervisor') {
        p.viewDashboard = true;
        p.updateCellStatus = true;
        p.viewInventory = true;
        p.viewVendors = true;
        p.editVendors = false;
        p.viewInvoices = false;
        p.viewPOs = false;
        p.viewPayroll = false;
        p.editWorkItems = false;
        p.editVentures = false;
        p.manageUsers = false;
        p.viewInstantReports = true;
        p.viewInventoryAudit = false;
        p.viewExpenditures = true;
        p.viewMaterialLeakage = true;
        p.releasePayroll = false;
        p.createCategory = false;
        p.reorderCells = false;
        p.viewDesignGenerator = false;
        p.viewStockPurchases = true;
        p.editStockPurchases = false;
        p.viewVendorPayments = true;
        p.viewContractorPayments = true;
        p.recordPayments = true;
        p.viewVendorOutstanding = true;
        p.manageContracts = false;
    } else if (role === 'manager' || role === 'admin') {
        p.viewDashboard = true;
        p.updateCellStatus = true;
        p.viewInventory = true;
        p.viewVendors = true;
        p.editVendors = true;
        p.viewInvoices = true;
        p.viewPOs = true;
        p.viewPayroll = true;
        p.editWorkItems = true;
        p.editVentures = true;
        p.manageUsers = role === 'admin';
        p.viewInstantReports = true;
        p.viewInventoryAudit = role === 'admin';
        p.viewExpenditures = true;
        p.viewMaterialLeakage = true;
        p.releasePayroll = role === 'admin';
        p.manageBudgets = role === 'admin';
        p.createCategory = true;
        p.reorderCells = true;
        p.viewDesignGenerator = true;
        p.viewStockPurchases = true;
        p.editStockPurchases = role === 'admin';
        p.viewVendorPayments = true;
        p.viewContractorPayments = role === 'admin';
        p.recordPayments = true;
        p.viewVendorOutstanding = true;
        p.manageContracts = role === 'admin';
    } else {
        // Unknown / fallback read-only
        p.viewDashboard = true;
        p.viewExpenditures = true;
    }
    return p;
}

async function checkSession() {
    try {
        const resp = await fetch('/api/me');
        const data = await resp.json();
        if (resp.ok && data.user) {
            currentUser = data.user;
            currentUserRole = data.role || 'supervisor';
            currentUserPermissions = buildPermissions(currentUserRole);
            if (els.userEmail) els.userEmail.textContent = currentUser;
            return true;
        }
        if (resp.ok) {
            // Authenticated but no user: redirect to login once
            window.location.href = '/login';
            return false;
        }
        // Server error: stay put and show an error so we don't loop
        showToast('Server error — please refresh later', true);
    } catch (e) {
        showToast('Network error — please refresh later', true);
    }
    return false;
}

els.signOutBtn.addEventListener('click', async () => {
    await fetch('/logout', { method: 'POST' });
    window.location.href = '/login';
});

// ========================
// Toast
// ========================
function showToast(message, isError = false) {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();
    const toast = document.createElement('div');
    toast.className = 'toast' + (isError ? ' error' : '');
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

function showSaveToast(message) {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();
    const toast = document.createElement('div');
    toast.className = 'toast saving';
    toast.innerHTML = '<span class="toast-spinner"></span>' + message;
    document.body.appendChild(toast);
    return toast;
}

function resolveSaveToast(toast, message, isError) {
    if (!toast) { showToast(message, isError); return; }
    toast.className = 'toast' + (isError ? ' error' : '');
    toast.innerHTML = isError ? ('&#9888; ' + message) : ('&#10003; ' + message);
    setTimeout(() => toast.remove(), 2500);
}

// ========================
// Init
// ========================
// ========================
// User Preferences (per-user work view layout)
// ========================
let userPrefs = { workCategoryOrder: [], workItemOrder: {} };
let _userPrefsSaveTimer = null;

async function loadUserPrefs() {
    try {
        const data = await apiGet('/api/user-prefs');
        if (data && typeof data === 'object') {
            userPrefs = {
                workCategoryOrder: data.workCategoryOrder || [],
                workItemOrder: data.workItemOrder || {}
            };
        }
    } catch (e) {
        // Non-critical — defaults will be used
        console.warn('Could not load user prefs:', e);
    }
}

async function saveUserPrefs() {
    try {
        await apiPost('/api/user-prefs', userPrefs);
    } catch (e) {
        console.warn('Could not save user prefs:', e);
    }
}

function saveUserPrefsDebounced() {
    if (_userPrefsSaveTimer) clearTimeout(_userPrefsSaveTimer);
    _userPrefsSaveTimer = setTimeout(saveUserPrefs, 500);
}

async function init() {
    const ok = await checkSession();
    if (!ok) return;

    // Load user preferences and ventures in parallel to cut init latency
    await Promise.all([
        loadUserPrefs().catch(() => {}),
        loadVentures().catch(err => {
            console.error('Ventures load failed on init:', err);
        })
    ]);

    // Preload commonly used data in parallel (non-blocking — panels will have data ready instantly)
    // Only preload if user has permissions
    if (currentUserPermissions) {
        const preloadTasks = [];
        if (currentUserPermissions.viewInvoices) {
            preloadTasks.push(ensureInvoicesLoaded().catch(() => {}));
            preloadTasks.push(ensureCategoriesLoaded().catch(() => {}));
        }
        if (currentUserPermissions.viewPOs) {
            preloadTasks.push(ensurePOsLoaded().catch(() => {}));
        }
        // Fire and forget — don't block init
        Promise.all(preloadTasks).catch(() => {});
    }

    await applyHashRoute();
    applyRoleBasedUI();
    startPolling();
}

function applyRoleBasedUI() {
    if (!currentUserPermissions) return;

    // Show/hide action buttons based on permissions (these are in-page buttons, not sidebar items)
    const hide = (id) => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    };
    const show = (id) => {
        const el = document.getElementById(id);
        if (el) el.style.display = '';
    };

    hide('addInvoiceBtn');
    hide('invoiceAddCategoryBtn');
    hide('addPOBtn');
    hide('addVendorBtn');
    hide('addVendorCategoryBtn');
    hide('vdAddVendorBtn');
    hide('addContractBtn');
    if (currentUserPermissions.viewInvoices) show('addInvoiceBtn');
    if (currentUserPermissions.viewInvoices) show('invoiceAddCategoryBtn');
    if (currentUserPermissions.viewPOs) show('addPOBtn');
    if (currentUserPermissions.editVendors) {
        show('addVendorBtn');
        show('addVendorCategoryBtn');
    }
    if (currentUserPermissions.editVendors) show('vdAddVendorBtn');
    if (currentUserPermissions.manageContracts) show('addContractBtn');

    // Dynamically render the sidebar from SIDEBAR_CONFIG
    renderSidebar();
}

function renderSidebar() {
    const nav = document.querySelector('.sidebar-nav');
    if (!nav) return;

    const config = SIDEBAR_CONFIG[currentUserRole] || SIDEBAR_CONFIG.supervisor;
    nav.innerHTML = '';

    config.sections.forEach((section, sIdx) => {
        const sectionEl = document.createElement('div');
        sectionEl.className = 'sidebar-accordion-section expanded';

        const header = document.createElement('button');
        header.className = 'sidebar-accordion-header';
        header.innerHTML = '<span class="accordion-chevron">\u25BC</span><span class="accordion-title">' + section.title + '</span>';
        header.addEventListener('click', () => {
            sectionEl.classList.toggle('expanded');
        });
        sectionEl.appendChild(header);

        const itemsWrap = document.createElement('div');
        itemsWrap.className = 'sidebar-accordion-items';

        section.items.forEach(item => {
            let el = document.getElementById(item.id);
            if (el && el.closest('#sidebarStaging')) {
                el.style.display = '';
                el.style.textDecoration = item.href ? 'none' : '';
                el.style.color = item.href ? 'inherit' : '';
            } else if (el) {
                // Element already in DOM elsewhere; clone it to preserve listeners
                const clone = el.cloneNode(true);
                clone.style.display = '';
                el = clone;
            } else {
                if (item.href) {
                    el = document.createElement('a');
                    el.href = item.href;
                    el.style.textDecoration = 'none';
                    el.style.color = 'inherit';
                } else {
                    el = document.createElement('button');
                }
                el.className = 'sidebar-nav-item';
                el.id = item.id;
                el.innerHTML = '<span class="nav-icon">' + item.icon + '</span><span class="nav-label">' + item.label + '</span>';
            }
            if (item.action === 'tracker' && !el._trackerBound) {
                el.addEventListener('click', () => {
                    const bcHome = document.getElementById('bcHome');
                    if (bcHome) bcHome.click();
                });
                el._trackerBound = true;
            }
            if (item.id === 'openDesignGeneratorBtn' && !el._dgBound) {
                el.addEventListener('click', () => {
                    if (typeof openDesignGeneratorPanel === 'function') openDesignGeneratorPanel();
                });
                el._dgBound = true;
            }
            itemsWrap.appendChild(el);
        });

        sectionEl.appendChild(itemsWrap);
        nav.appendChild(sectionEl);
    });

    // Bind nav item clicks for mobile sidebar close + active state
    const MODAL_OPENING_ITEMS = new Set(['settingsBtn', 'editModeBtn', 'manageUsersBtn']);
    nav.querySelectorAll('.sidebar-nav-item').forEach(item => {
        item.addEventListener('click', () => {
            setActiveNav(item.id);
            // Close all open modals when navigating via sidebar — except for items that open modals
            if (!MODAL_OPENING_ITEMS.has(item.id)) {
                document.querySelectorAll('.modal.show').forEach(m => m.classList.remove('show'));
            }
            if (window.innerWidth <= 900) {
                const app = document.getElementById('app');
                if (app) app.classList.remove('sidebar-open');
            }
        });
    });

    // Event delegation for panel-opening nav items
    const NAV_PANEL_HANDLERS = {
        'openInvoicesBtn': () => { if (typeof openInvoicesPanel === 'function') openInvoicesPanel(); },
        'openAttendanceBtn': () => { if (typeof openAttendancePanel === 'function') openAttendancePanel(); },
        'openVendorsBtn': () => { if (typeof openVendorDirPanel === 'function') openVendorDirPanel(); else { const m = document.getElementById('vendorDirModal'); if (m) m.classList.add('show'); } },
        'openInventoryBtn': () => { if (typeof openInventoryRegisterPanel === 'function') openInventoryRegisterPanel(); else if (typeof openInventoryPanel === 'function') openInventoryPanel(); },
        'openVentureAnalysisBtn': () => { if (typeof openVentureAnalysisPanel === 'function') openVentureAnalysisPanel(); },
        'openDayBookBtn': () => { if (typeof openDayBookPanel === 'function') openDayBookPanel(); },
        'openPOBtn': () => { if (typeof openPOPanel === 'function') openPOPanel(); },
        'openReportsBtn': () => { if (typeof openReportsPanel === 'function') openReportsPanel(); },
        'openExpenditureBtn': () => { if (typeof openExpenditurePanel === 'function') openExpenditurePanel(); },
        'openInstantReportsBtn': () => { if (typeof openInstantReportsPanel === 'function') openInstantReportsPanel(); },
        'openInventoryAuditBtn': () => { if (typeof openInventoryAuditPanel === 'function') openInventoryAuditPanel(); },
        'openDesignGeneratorBtn': () => { if (typeof openDesignGeneratorPanel === 'function') openDesignGeneratorPanel(); },
        'openLenderReportBtn': () => { openLenderReportModal(); },
        'openContractorPaymentsBtn': () => { if (typeof openContractorPaymentsPanel === 'function') openContractorPaymentsPanel(); },
    };

    nav.addEventListener('click', (e) => {
        const item = e.target.closest('.sidebar-nav-item');
        if (!item) return;
        const handler = NAV_PANEL_HANDLERS[item.id];
        if (handler) handler();
    });

    // Bind overview button if present
    const overviewBtn = document.getElementById('sidebarOverview');
    if (overviewBtn && !overviewBtn._bound) {
        overviewBtn.addEventListener('click', () => {
            navigateTo('#/overview');
            if (typeof renderOverviewPage === 'function') renderOverviewPage();
        });
        overviewBtn._bound = true;
    }

    // Bind dashboard button if present
    const dashBtn = document.getElementById('sidebarDashboard');
    if (dashBtn && !dashBtn._bound) {
        dashBtn.addEventListener('click', () => {
            const bcHome = document.getElementById('bcHome');
            if (bcHome) bcHome.click();
        });
        dashBtn._bound = true;
    }
}

function setActiveNav(id) {
    activeNavId = id;
    document.querySelectorAll('.sidebar-nav-item').forEach(el => {
        el.classList.toggle('active', el.id === id);
    });
}

async function preloadCells() {
    if (currentVenture && currentVenture.id) {
        const ventureCells = await apiGet('/api/cells?venture_id=' + encodeURIComponent(currentVenture.id));
        if (ventureCells) Object.assign(cellsCache, ventureCells);
    }
    // If no venture selected, defer loading until a venture is opened
    // (ensureCellsInCache will lazy-load when needed)
}

let pollInterval = null;

function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollData, 10000);
}

function patchCellsInDOM(changedKeys) {
    const changedSet = new Set(changedKeys);
    const buttons = document.querySelectorAll('.cell-btn[data-cell-id]');
    buttons.forEach(btn => {
        const ck = btn.dataset.cellId;
        if (!changedSet.has(ck)) return;
        // Don't patch cells with pending bulk paint changes
        if (bulkPendingChanges.has(ck)) return;
        const cellData = cellsCache[ck];
        const color = cellData?.color || null;
        btn.className = 'cell-btn ' + (color || 'red');
        const existingIndicator = btn.querySelector('.remarks-image-indicator');
        if (existingIndicator) existingIndicator.remove();
        const imgCount = (cellData?.remarkImages || []).length;
        const imgIndicator = createImageIndicator(imgCount);
        if (imgIndicator) btn.appendChild(imgIndicator);
    });
}

function diffByIds(oldList, newList) {
    const oldMap = new Map((oldList || []).map(x => [x.id, x]));
    const newMap = new Map((newList || []).map(x => [x.id, x]));
    const added = [];
    const removed = [];
    const modified = [];
    for (const [id, item] of newMap) {
        if (!oldMap.has(id)) added.push(id);
        else if (JSON.stringify(oldMap.get(id)) !== JSON.stringify(item)) modified.push(id);
    }
    for (const [id] of oldMap) {
        if (!newMap.has(id)) removed.push(id);
    }
    return { added, removed, modified, hasStructuralChange: added.length > 0 || removed.length > 0 };
}

function patchInvoiceCardsInPlace(changedIds) {
    const changedSet = new Set(changedIds);
    const grid = document.getElementById('invoiceCardsGrid');
    if (!grid) return;
    grid.querySelectorAll('.invoice-card[data-invoice-id]').forEach(card => {
        const id = card.dataset.invoiceId;
        if (!changedSet.has(id)) return;
        const inv = allInvoices.find(i => i.id === id);
        if (!inv) { card.remove(); return; }
        const venture = venturesList.find(v => v.id === inv.ventureId);
        const ventureName = venture ? venture.name : (inv.ventureName || 'Unknown');
        const dateDisplay = inv.purchaseDate ? new Date(inv.purchaseDate + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '\u2014';
        const amountDisplay = inv.amount ? '\u20B9' + parseFloat(inv.amount).toLocaleString('en-IN', { maximumFractionDigits: 2 }) : '\u2014';
        const attachCount = (inv.attachments || []).length;
        card.innerHTML = `
            <div class="invoice-card-header">
                <span class="invoice-card-category">${escapeHtml(inv.category || '\u2014')}</span>
                <span class="invoice-card-venture">${escapeHtml(ventureName)}</span>
            </div>
            <div class="invoice-card-amount">${amountDisplay}</div>
            <div class="invoice-card-meta">
                <span>\u{1F4C5} ${dateDisplay}</span>
                ${inv.paymentMode ? `<span>\u{1F4B3} ${escapeHtml(inv.paymentMode)}</span>` : ''}
                ${inv.vendor ? `<span>\u{1F3E2} ${escapeHtml(inv.vendor)}</span>` : ''}
            </div>
            <div class="invoice-card-reason">${escapeHtml(inv.reason || '')}</div>
            ${attachCount > 0 ? `<div class="invoice-card-attach">\u{1F4CE} ${attachCount} attachment${attachCount > 1 ? 's' : ''}</div>` : ''}
        `;
    });
    // Update summary bar
    const summaryBar = document.getElementById('invoiceSummaryBar');
    if (summaryBar) {
        const totalAmount = allInvoices.reduce((sum, inv) => sum + (parseFloat(inv.amount) || 0), 0);
        summaryBar.innerHTML = `
            <span class="inv-summary-count">${allInvoices.length} invoice${allInvoices.length !== 1 ? 's' : ''}</span>
            <span class="inv-summary-sep">\u00B7</span>
            <span class="inv-summary-total">Total: \u20B9${totalAmount.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</span>
        `;
    }
}

function patchPOCardsInPlace(changedIds) {
    const changedSet = new Set(changedIds);
    const grid = document.getElementById('poCardsGrid');
    if (!grid) return;
    grid.querySelectorAll('.po-card[data-po-id]').forEach(card => {
        const id = card.dataset.poId;
        if (!changedSet.has(id)) return;
        const po = allPOs.find(p => p.id === id);
        if (!po) { card.remove(); return; }
        const vendor = (typeof loadVendors === 'function' ? loadVendors() : allVendors || []).find(v => v.id === po.vendorId);
        const venture = venturesList.find(v => v.id === po.ventureId);
        const { base, paid, outstanding } = (typeof getPOBalance === 'function') ? getPOBalance(po) : { base: 0, paid: 0, outstanding: 0 };
        const flagged = (typeof isPOFlaggedUnpaid === 'function') ? isPOFlaggedUnpaid(po) : false;
        const dateDisplay = po.orderDate ? new Date(po.orderDate + 'T00:00:00').toLocaleDateString('en-IN', {day:'numeric',month:'short',year:'numeric'}) : '\u2014';
        const statusColor = (typeof PO_STATUS_COLORS !== 'undefined' && PO_STATUS_COLORS[po.status]) || '#888';
        card.className = 'po-card' + (flagged ? ' po-card-flagged' : '');
        card.innerHTML = `
            <div class="po-card-top">
                <span class="po-card-number">${escapeHtml(po.poNumber || '\u2014')}</span>
                <span class="po-card-status" style="background:${statusColor};">${(typeof PO_STATUS_LABELS !== 'undefined' && PO_STATUS_LABELS[po.status]) || po.status}</span>
            </div>
            <div class="po-card-vendor">${escapeHtml(vendor ? vendor.name : '\u2014')}</div>
            <div class="po-card-desc">${escapeHtml((po.description || '').substring(0, 80))}${(po.description||'').length > 80 ? '\u2026' : ''}</div>
            <div class="po-card-meta">
                <span>\u{1F4C5} ${dateDisplay}</span>
                ${po.orderType ? `<span>\u{1F69F} ${escapeHtml(po.orderType)}</span>` : ''}
                ${venture ? `<span>\u{1F30F} ${escapeHtml(venture.name)}</span>` : ''}
            </div>
            <div class="po-card-financials">
                <div class="po-fin-row"><span class="po-fin-label">Billed</span><span class="po-fin-value">${base ? '\u20B9' + base.toLocaleString('en-IN', {maximumFractionDigits:0}) : '\u2014'}</span></div>
                <div class="po-fin-row"><span class="po-fin-label">Paid</span><span class="po-fin-value po-fin-paid">${paid ? '\u20B9' + paid.toLocaleString('en-IN', {maximumFractionDigits:0}) : '\u2014'}</span></div>
                <div class="po-fin-row"><span class="po-fin-label">Outstanding</span><span class="po-fin-value ${outstanding > 0 ? 'po-fin-outstanding' : 'po-fin-clear'}">${outstanding > 0 ? '\u20B9' + outstanding.toLocaleString('en-IN', {maximumFractionDigits:0}) : '\u2713 Clear'}</span></div>
            </div>
            ${flagged ? '<div class="po-card-unpaid-flag">\u26A0 Delivered \u2014 payment pending</div>' : ''}
        `;
    });
}

async function pollData() {
    // Skip polling while user is actively editing (any modal open)
    if (document.querySelector('.modal.show')) return;

    const invoicesVisible = document.getElementById('invoicesPanel')?.style.display !== 'none';
    const dashboardVisible = document.getElementById('venturesDashboard')?.style.display !== 'none';
    const poVisible = document.getElementById('poPanel')?.style.display !== 'none';
    const dirModal = document.getElementById('vendorDirModal');
    const dirModalOpen = dirModal && dirModal.classList.contains('show') && _vendorsLoaded;

    // Build a list of parallel fetch tasks based on what's visible
    const tasks = [];

    // Ventures — always poll
    tasks.push({ key: 'ventures', fn: () => apiGet('/api/ventures') });

    // Categories — always poll if loaded (so new categories from admin replicate to all devices)
    if (_categoriesLoaded) {
        tasks.push({ key: 'categories', fn: () => apiGet('/api/settings/invoice_categories') });
    }

    // Invoices — only if visible, loaded, and user has access
    if (invoicesVisible && _invoicesLoaded && currentUserPermissions.viewInvoices) {
        tasks.push({ key: 'invoices', fn: () => apiGet('/api/invoices') });
    }

    // POs — only if visible, loaded, and user has access
    if (poVisible && _posLoaded && currentUserPermissions.viewPOs) {
        tasks.push({ key: 'pos', fn: () => apiGet('/api/pos') });
    }

    // Vendors — only if vendor directory modal is open
    if (dirModalOpen) {
        tasks.push({ key: 'vendors', fn: () => apiGet('/api/vendors') });
    }

    // Cells — only if tracker or overview visible and no pending saves or bulk mode
    const tracker = document.getElementById('trackerView');
    const trackerVisible = tracker && tracker.style.display !== 'none';
    const overviewVisible = document.getElementById('overviewPage')?.style.display !== 'none';
    const skipCells = pendingSaves.size > 0 || inFlightSaves > 0 || bulkMode;
    if ((trackerVisible || overviewVisible) && !skipCells) {
        let cellsUrl = '/api/cells';
        if (currentVenture && currentVenture.id) {
            cellsUrl += '?venture_id=' + encodeURIComponent(currentVenture.id);
        }
        tasks.push({ key: 'cells', fn: () => apiGet(cellsUrl) });
    }

    // Fire all fetches in parallel
    const results = await Promise.allSettled(tasks.map(t => t.fn()));
    const data = {};
    tasks.forEach((t, i) => { data[t.key] = results[i]; });

    let changed = false;

    // Process ventures
    if (data.ventures && data.ventures.status === 'fulfilled' && data.ventures.value) {
        const fresh = data.ventures.value;
        if (JSON.stringify(fresh) !== JSON.stringify(venturesList)) {
            venturesList = fresh;
            refreshCurrentVentureFromList();
            changed = true;
            if (dashboardVisible) renderVentureDashboard();
        }
    }

    // Process categories
    if (data.categories && data.categories.status === 'fulfilled' && data.categories.value) {
        const fresh = data.categories.value;
        if (JSON.stringify(fresh) !== JSON.stringify(allCategories)) {
            allCategories = fresh;
            changed = true;
            // Update filter dropdown if invoices panel is open
            if (invoicesVisible) {
                renderInvoiceCards();
                if (typeof populateInvoiceFilterCategories === 'function') {
                    populateInvoiceFilterCategories();
                }
            }
            // Update datalist for invoice form
            const dl = document.getElementById('invoiceCategoryList');
            if (dl) {
                dl.innerHTML = '';
                allCategories.forEach(c => {
                    const opt = document.createElement('option');
                    opt.value = c;
                    dl.appendChild(opt);
                });
            }
        }
    }

    // Process invoices
    if (data.invoices && data.invoices.status === 'fulfilled') {
        const fresh = data.invoices.value || [];
        const diff = diffByIds(allInvoices, fresh);
        if (diff.added.length > 0 || diff.modified.length > 0 || diff.removed.length > 0) {
            allInvoices = fresh;
            changed = true;
            if (diff.hasStructuralChange) {
                renderInvoiceCards();
            } else {
                patchInvoiceCardsInPlace(diff.modified);
            }
        }
    }

    // Process POs
    if (data.pos && data.pos.status === 'fulfilled') {
        const fresh = data.pos.value || [];
        const diff = diffByIds(allPOs, fresh);
        if (diff.added.length > 0 || diff.modified.length > 0 || diff.removed.length > 0) {
            allPOs = fresh;
            changed = true;
            if (diff.hasStructuralChange) {
                renderPOCards();
            } else {
                patchPOCardsInPlace(diff.modified);
            }
        }
    }

    // Process vendors
    if (data.vendors && data.vendors.status === 'fulfilled') {
        const fresh = data.vendors.value || [];
        if (JSON.stringify(fresh) !== JSON.stringify(allVendors)) {
            allVendors = fresh;
            changed = true;
            renderVendorDirList();
        }
    }

    // Process cells
    if (data.cells && data.cells.status === 'fulfilled' && data.cells.value) {
        const fresh = data.cells.value;
        let cellsChanged = false;
        const changedKeys = [];
        for (const key in fresh) {
            // Don't overwrite cells with pending bulk paint changes
            if (bulkPendingChanges.has(key)) continue;
            const oldCell = cellsCache[key];
            const newCell = fresh[key];
            if (!oldCell || oldCell.__notFound) {
                cellsCache[key] = newCell;
                changedKeys.push(key);
                cellsChanged = true;
            } else {
                // Shallow comparison of key fields instead of full JSON.stringify
                if (oldCell.color !== newCell.color ||
                    oldCell.remarks !== newCell.remarks ||
                    (oldCell.remarkImages?.length || 0) !== (newCell.remarkImages?.length || 0) ||
                    oldCell.updated_at !== newCell.updated_at) {
                    cellsCache[key] = newCell;
                    changedKeys.push(key);
                    cellsChanged = true;
                }
            }
        }
        const freshKeys = new Set(Object.keys(fresh));
        const venturePrefix = currentVenture ? currentVenture.id + '_' : null;
        for (const ck of Object.keys(cellsCache)) {
            if (venturePrefix && !ck.startsWith(venturePrefix)) continue;
            if (!freshKeys.has(ck) && cellsCache[ck] && !cellsCache[ck]?.__notFound) {
                cellsCache[ck] = CELL_NOT_FOUND;
                changedKeys.push(ck);
                cellsChanged = true;
            }
        }
        if (cellsChanged) {
            changed = true;
            if (overviewVisible) {
                if (typeof renderOverviewPage === 'function') renderOverviewPage();
            } else if (currentView === 'work' || currentView === 'super') {
                patchCellsInDOM(changedKeys);
            } else if (currentView === 'pending') {
                await renderPendingView();
            }
        }
    }
}

// ========================
// Immediate sync triggers (visibility, focus, online)
// ========================
function triggerImmediateSync() {
    // Sync whenever the app becomes active again, as long as no modal is open.
    if (!document.querySelector('.modal.show')) {
        pollData();
    }
}

document.addEventListener('visibilitychange', () => {
    if (!document.hidden) triggerImmediateSync();
});

window.addEventListener('focus', triggerImmediateSync);

// ========================
// Lender Report Modal
// ========================
function openLenderReportModal() {
    const modal = document.getElementById('lenderReportModal');
    const ventureSelect = document.getElementById('lenderReportVenture');
    const dateInput = document.getElementById('lenderReportDate');
    if (!modal) return;

    // Populate ventures dropdown
    if (ventureSelect) {
        ventureSelect.innerHTML = '<option value="">Select a project</option>';
        (venturesList || []).forEach(v => {
            const opt = document.createElement('option');
            opt.value = v.id;
            opt.textContent = v.name || v.id;
            ventureSelect.appendChild(opt);
        });
    }

    // Default date to today
    if (dateInput && !dateInput.value) {
        dateInput.value = new Date().toISOString().split('T')[0];
    }

    modal.classList.add('show');
}

function closeLenderReportModal() {
    const modal = document.getElementById('lenderReportModal');
    if (modal) modal.classList.remove('show');
}

function generateLenderReport() {
    const ventureSelect = document.getElementById('lenderReportVenture');
    const dateInput = document.getElementById('lenderReportDate');
    const financialsCheckbox = document.getElementById('lenderReportIncludeFinancials');
    const ventureId = ventureSelect ? ventureSelect.value : '';
    if (!ventureId) {
        alert('Please select a project.');
        return;
    }
    const date = dateInput ? dateInput.value : '';
    const includeFinancials = financialsCheckbox ? financialsCheckbox.checked : true;
    const url = '/api/reports/lender-report/' + ventureId +
                (date ? '?date=' + encodeURIComponent(date) : '') +
                (date ? '&' : '?') + 'include_financials=' + includeFinancials;
    window.location.href = url;
    closeLenderReportModal();
}

(function bindLenderReportModal() {
    const closeBtn = document.getElementById('closeLenderReport');
    const cancelBtn = document.getElementById('cancelLenderReport');
    const genBtn = document.getElementById('generateLenderReport');
    const modal = document.getElementById('lenderReportModal');

    if (closeBtn) closeBtn.addEventListener('click', closeLenderReportModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeLenderReportModal);
    if (genBtn) genBtn.addEventListener('click', generateLenderReport);
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeLenderReportModal();
        });
    }
})();

// ========================
// Sidebar toggle / collapse
// ========================
function initSidebarToggle() {
    const sidebarCollapse = document.getElementById('sidebarCollapse');
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebarScrim = document.getElementById('sidebarScrim');
    const app = document.getElementById('app');
    const appSidebar = document.getElementById('appSidebar');
    const appMain = document.getElementById('appMain');

    if (sidebarCollapse && appSidebar && appMain) {
        sidebarCollapse.addEventListener('click', () => {
            const isCollapsed = appSidebar.classList.toggle('collapsed');
            if (isCollapsed) {
                appMain.classList.add('sidebar-collapsed');
                appMain.style.marginLeft = '';
                appSidebar.style.width = '';
            } else {
                appMain.classList.remove('sidebar-collapsed');
                appMain.style.marginLeft = '248px';
                appSidebar.style.width = '248px';
            }
            try { localStorage.setItem('sidebarCollapsed', isCollapsed ? '1' : '0'); } catch (e) {}
        });
        try {
            const saved = localStorage.getItem('sidebarCollapsed');
            if (saved === '1') {
                appSidebar.classList.add('collapsed');
                appMain.classList.add('sidebar-collapsed');
            }
        } catch (e) {}
    }

    if (sidebarToggle && app) {
        sidebarToggle.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            // Close any open modals so sidebar is accessible
            document.querySelectorAll('.modal.show').forEach(m => m.classList.remove('show'));
            app.classList.add('sidebar-open');
        });
        sidebarToggle.addEventListener('touchend', (e) => {
            e.preventDefault();
            e.stopPropagation();
            document.querySelectorAll('.modal.show').forEach(m => m.classList.remove('show'));
            app.classList.add('sidebar-open');
        });
    }
    if (sidebarScrim && app) {
        sidebarScrim.addEventListener('click', () => app.classList.remove('sidebar-open'));
    }
    // Close sidebar when clicking a nav item on mobile
    if (appSidebar && app) {
        appSidebar.addEventListener('click', (e) => {
            if (window.innerWidth <= 900 && e.target.closest('.sidebar-nav-item')) {
                app.classList.remove('sidebar-open');
            }
        });
    }

    // Clean up desktop/mobile state on viewport change
    let _prevMobile = window.innerWidth <= 900;
    window.addEventListener('resize', () => {
        const isMobile = window.innerWidth <= 900;
        if (isMobile !== _prevMobile) {
            _prevMobile = isMobile;
            if (isMobile) {
                // Switching to mobile: remove desktop sidebar state
                if (app) app.classList.remove('sidebar-open');
                if (appSidebar) {
                    appSidebar.classList.remove('collapsed');
                }
                if (appMain) {
                    appMain.classList.remove('sidebar-collapsed');
                    appMain.style.marginLeft = '';
                }
            } else {
                // Switching to desktop: restore sidebar
                if (app) app.classList.add('sidebar-open');
                if (appSidebar) {
                    try {
                        const saved = localStorage.getItem('sidebarCollapsed');
                        if (saved === '1') {
                            appSidebar.classList.add('collapsed');
                        } else {
                            appSidebar.classList.remove('collapsed');
                        }
                    } catch (e) {}
                }
                if (appMain) {
                    try {
                        const saved = localStorage.getItem('sidebarCollapsed');
                        if (saved === '1') {
                            appMain.classList.add('sidebar-collapsed');
                            appMain.style.marginLeft = '';
                        } else {
                            appMain.classList.remove('sidebar-collapsed');
                            appMain.style.marginLeft = '248px';
                        }
                    } catch (e) {}
                }
            }
        }
    });
}
initSidebarToggle();

window.addEventListener('online', triggerImmediateSync);

// Prevent mouse wheel from changing number inputs (avoids accidental negative values)
document.addEventListener('wheel', function(e) {
    if (document.activeElement && document.activeElement.type === 'number') {
        e.preventDefault();
        document.activeElement.blur();
    }
}, { passive: false });

// Clamp negative values to 0 for all number inputs
document.addEventListener('input', function(e) {
    if (e.target.type === 'number') {
        if (e.target.value < 0) e.target.value = 0;
    }
});
document.addEventListener('change', function(e) {
    if (e.target.type === 'number') {
        if (e.target.value < 0) e.target.value = 0;
    }
});
