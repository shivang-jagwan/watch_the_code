import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './styles.css'
import { AuthProvider } from './auth/AuthProvider'
import { Layout } from './components/Layout'
import { Dashboard } from './pages/Dashboard'
import { LoginPage } from './pages/LoginPage'
import { SignupPage } from './pages/SignupPage'
import { Teachers } from './pages/Teachers'
import { TimeSlots } from './pages/TimeSlots'
import { GenerateTimetable } from './pages/GenerateTimetable'
import { Programs } from './pages/Programs'
import { Subjects } from './pages/Subjects'
import { Sections } from './pages/Sections'
import { Rooms } from './pages/Rooms'
import { Curriculum } from './pages/Curriculum'
import { ElectiveBlocks } from './pages/ElectiveBlocks'
import { CombinedClasses } from './pages/CombinedClasses'
import { SpecialAllotments } from './pages/SpecialAllotments'
import { Conflicts } from './pages/Conflicts'
import { Timetable } from './pages/Timetable'
import { TimetablePrint } from './pages/TimetablePrint'
import { PrintableTimetable } from './pages/PrintableTimetable'
import { TimetablePrintAllSections } from './pages/TimetablePrintAllSections'
import { TimetablePrintAllRooms } from './pages/TimetablePrintAllRooms'
import { TimetablePrintAllFaculty } from './pages/TimetablePrintAllFaculty'
import { OfficialPrintAllSections } from './pages/OfficialPrintAllSections'
import { OfficialPrintAllFaculty } from './pages/OfficialPrintAllFaculty'
import { OfficialPrintAllRooms } from './pages/OfficialPrintAllRooms'
import { RequireAuth } from './routes/RequireAuth'
import { RedirectIfAuth } from './routes/RedirectIfAuth'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route
            path="/login"
            element={
              <RedirectIfAuth>
                <LoginPage />
              </RedirectIfAuth>
            }
          />

          <Route
            path="/signup"
            element={
              <RedirectIfAuth>
                <SignupPage />
              </RedirectIfAuth>
            }
          />

          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />

            <Route path="/programs" element={<Programs />} />
            <Route path="/teachers" element={<Teachers />} />
            <Route path="/subjects" element={<Subjects />} />
            <Route path="/sections" element={<Sections />} />
            <Route path="/rooms" element={<Rooms />} />
            <Route path="/time-slots" element={<TimeSlots />} />
            <Route path="/curriculum" element={<Curriculum />} />
            <Route path="/elective-blocks" element={<ElectiveBlocks />} />
            <Route path="/combined-classes" element={<CombinedClasses />} />
            <Route path="/special-allotments" element={<SpecialAllotments />} />
            <Route path="/generate" element={<GenerateTimetable />} />
            <Route path="/conflicts" element={<Conflicts />} />
            <Route path="/timetable" element={<Timetable />} />
            <Route path="/timetable/print" element={<TimetablePrint />} />
            <Route path="/timetable/print-official" element={<PrintableTimetable />} />
            <Route path="/timetable/print-all/sections" element={<TimetablePrintAllSections />} />
            <Route path="/timetable/print-all/rooms" element={<TimetablePrintAllRooms />} />
            <Route path="/timetable/print-all/faculty" element={<TimetablePrintAllFaculty />} />
            <Route path="/print/sections" element={<OfficialPrintAllSections />} />
            <Route path="/print/faculty" element={<OfficialPrintAllFaculty />} />
            <Route path="/print/rooms" element={<OfficialPrintAllRooms />} />

            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Route>

          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  </React.StrictMode>,
)
