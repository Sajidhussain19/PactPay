"use client";

import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { StateBadge } from "@/components/state-badge";
import { STATE_INFO, date, money } from "@/lib/format";
import type { ContractOut, MilestoneState } from "@/lib/types";

ModuleRegistry.registerModules([AllCommunityModule]);

interface Row {
  milestoneId: string;
  contractId: string;
  contract: string;
  milestone: string;
  state: MilestoneState;
  amount: number;
  currency: string;
  money: string;
  due: string;
  counterparty: string;
}

const theme = themeQuartz.withParams({
  accentColor: "#059669",
  borderRadius: 8,
  headerBackgroundColor: "#f8fafc",
  fontFamily: "inherit",
});

export function MilestoneGrid({ contracts, viewer }: { contracts: ContractOut[]; viewer: "client" | "freelancer" | "admin" }) {
  const router = useRouter();

  const rows = useMemo<Row[]>(
    () =>
      contracts.flatMap((contract) =>
        contract.milestones.map((m) => ({
          milestoneId: m.id,
          contractId: contract.id,
          contract: contract.title,
          milestone: `${m.code} · ${m.title}`,
          state: m.state,
          amount: Number(m.amount),
          currency: m.currency,
          money: STATE_INFO[m.state].money,
          due: m.due_date,
          counterparty:
            viewer === "freelancer" ? contract.client.name : (contract.freelancer?.name ?? "Not assigned"),
        })),
      ),
    [contracts, viewer],
  );

  const columns = useMemo<ColDef<Row>[]>(
    () => [
      { field: "contract", headerName: "Contract", flex: 2, minWidth: 200 },
      { field: "milestone", headerName: "Milestone", flex: 2, minWidth: 180 },
      {
        field: "state",
        headerName: "Status",
        width: 150,
        cellRenderer: (params: { value: MilestoneState }) => <StateBadge state={params.value} />,
      },
      {
        field: "amount",
        headerName: "Amount",
        width: 120,
        type: "rightAligned",
        valueFormatter: (params) => money(params.value, params.data?.currency),
      },
      { field: "money", headerName: "Money", flex: 1, minWidth: 170 },
      { field: "due", headerName: "Due", width: 130, valueFormatter: (params) => date(params.value) },
      {
        field: "counterparty",
        headerName: viewer === "freelancer" ? "Client" : "Freelancer",
        flex: 1,
        minWidth: 150,
      },
    ],
    [viewer],
  );

  return (
    <div className="h-[420px] w-full">
      <AgGridReact<Row>
        theme={theme}
        rowData={rows}
        columnDefs={columns}
        rowHeight={48}
        defaultColDef={{ sortable: true, filter: true, resizable: true }}
        onRowClicked={(event) => event.data && router.push(`/milestones/${event.data.milestoneId}`)}
        rowClass="cursor-pointer"
        overlayNoRowsTemplate="No milestones yet"
      />
    </div>
  );
}
